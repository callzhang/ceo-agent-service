"""Offline Email13 copy proof against the published Store19/Email51 predecessor."""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

from app import action_storage
from app.action_email_storage import (
    SOURCE_TABLES, capture_email_action_source, migrate_email_action_schema,
    restore_email_action_schema, _projection, _rows, _types, _columns, _sequence,
    _fingerprint, _set_sequence,
)
from app.action_email_definitions import (EMAIL_AUTOINCREMENT_TYPES, EMAIL_COMPOSITE_KEYS,
                                          EMAIL_ROWID_TYPES, EMAIL_TYPES_ORDER)

PUBLISHED_SCHEMA_REF = "eb797e6bd35de695c269054a2e697af8fde2baa5"
PRODUCTION_ROOT = Path.home() / "Services/ceo-agent-service"
BINDING = Path(__file__).resolve().parents[1] / "docs/superpowers/specs/2026-10-14-database-redesign-email-actions-published-source.json"


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _marker(db):
    return db.execute("select value,updated_at from main.service_state where key='store_schema_version'").fetchone()


def _email_ledger(db):
    return tuple(db.execute("select version,applied_at from main.email_schema_migrations order by version"))


def _consistency(db, expected_fk=None):
    assert db.execute("pragma quick_check").fetchone() == ("ok",)
    assert db.execute("pragma integrity_check").fetchone() == ("ok",)
    fk = tuple(db.execute("pragma foreign_key_check"))
    if expected_fk is not None:
        assert fk == expected_fk, "foreign-key findings changed"
    return fk


def _digest(db, name, fixture_ids=None):
    columns = tuple(row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(name)})") if row[6] == 0)
    selected = ",".join(map(_quote, columns))
    storage = ",".join(f"typeof({_quote(c)})" for c in columns)
    digest = hashlib.sha256()
    count = 0
    try:
        db.execute(f"select rowid from main.{_quote(name)} limit 0")
        ordering = "rowid"
    except sqlite3.OperationalError:
        ordering = ",".join(str(i) for i in range(1, len(columns) + 1))
    predicate = ""
    parameters = ()
    if fixture_ids and name == "email_classifications":
        predicate = " where id not in (" + ",".join("?" for _ in fixture_ids) + ")"
        parameters = tuple(fixture_ids)
    elif fixture_ids and name == "email_messages":
        predicate = " where stable_message_identity not in (" + ",".join("?" for _ in fixture_ids) + ")"
        parameters = tuple(f"copy-only-account:message-id:<copy-{ident}@example.test>"
                           for ident in fixture_ids)
    elif fixture_ids and name == "email_accounts":
        predicate = " where account_id<>'copy-only-account'"
    for values in db.execute(f"select {selected},{storage} from main.{_quote(name)}"
                             f"{predicate} order by {ordering}", parameters):
        digest.update(pickle.dumps(tuple(values), protocol=4))
        count += 1
    return count, digest.digest()


def _unrelated(db, fixture_ids=None):
    names = [row[0] for row in db.execute(
        "select name from main.sqlite_schema where type='table' and name not like 'sqlite_%' order by name")]
    return {name: _digest(db, name, fixture_ids) for name in names
            if name not in (*SOURCE_TABLES, "actions")}


def _fixture_rows(db, fixture_ids):
    predicates = {
        "email_accounts": ("account_id=?", ("copy-only-account",)),
        "email_classifications": ("id in (" + ",".join("?" for _ in fixture_ids) + ")",
                                  fixture_ids),
        "email_messages": ("stable_message_identity in (" +
                           ",".join("?" for _ in fixture_ids) + ")",
                           tuple(f"copy-only-account:message-id:<copy-{ident}@example.test>"
                                 for ident in fixture_ids)),
    }
    result = {}
    for name, (predicate, args) in predicates.items():
        columns = _columns(db, name)
        selected = ",".join(map(_quote, columns))
        storage = ",".join(f"typeof({_quote(column)})" for column in columns)
        result[name] = tuple(db.execute(
            f"select {selected},{storage} from main.{_quote(name)} where {predicate} order by rowid",
            args))
    return result


def _public_seed_clock(value, start, end):
    assert isinstance(value, str)
    observed = datetime.fromisoformat(value)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed = observed.astimezone(timezone.utc)
    assert start - timedelta(seconds=2) <= observed <= end + timedelta(seconds=2)
    return "<public-clock>"


def _normalized_seed_fixture(db, fixture_ids, start, end):
    rows = _fixture_rows(db, fixture_ids)
    normalized = {}
    for name, entries in rows.items():
        columns = _columns(db, name)
        time_positions = (tuple(columns.index(column) for column in ("created_at", "updated_at")
                                if column in columns)
                          if name in ("email_messages", "email_classifications") else ())
        items = []
        for entry in entries:
            values = list(entry)
            for position in time_positions:
                values[position] = _public_seed_clock(values[position], start, end)
            items.append(tuple(values))
        normalized[name] = tuple(items)
    return normalized


def _normalized_seed_source(db, fixture_ids, start, end):
    facts = _source_facts(db)
    normalized = {}
    for name, (rows, types) in facts.items():
        columns = (("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns(db, name)
        classification_position = columns.index("classification_id") if "classification_id" in columns else None
        time_positions = (tuple(columns.index(column) for column in ("created_at", "updated_at")
                                if column in columns) if name == "email_actions" else ())
        converted = []
        for row in rows:
            values = list(row)
            if classification_position is not None and row[classification_position] in fixture_ids:
                for position in time_positions:
                    values[position] = _public_seed_clock(values[position], start, end)
            converted.append(tuple(values))
        normalized[name] = (tuple(converted), tuple(types))
    return normalized


def _normalized_feedback_source(db, facts, start, end):
    normalized = dict(facts)
    name = "email_feedback_requests"
    columns = ("rowid",) + _columns(db, name)
    request_position = columns.index("feedback_request_id")
    clock_position = columns.index("applied_at")
    rows = []
    for row in facts[name][0]:
        values = list(row)
        if row[request_position] == "copy-only-feedback":
            values[clock_position] = _public_seed_clock(values[clock_position], start, end)
        rows.append(tuple(values))
    normalized[name] = (tuple(rows), tuple(facts[name][1]))
    return normalized


def _normalized_feedback_fixture(db, fixture_ids, start, end):
    rows = _fixture_rows(db, fixture_ids)
    columns = _columns(db, "email_classifications")
    id_position = columns.index("id")
    clock_positions = (columns.index("updated_at"), columns.index("confirmed_at"))
    normalized = dict(rows)
    converted = []
    for row in rows["email_classifications"]:
        values = list(row)
        if row[id_position] == fixture_ids[0]:
            for position in clock_positions:
                values[position] = _public_seed_clock(values[position], start, end)
        converted.append(tuple(values))
    normalized["email_classifications"] = tuple(converted)
    return normalized


def _actions(db, columns):
    selected = ",".join(map(_quote, columns))
    storage = ",".join(f"typeof({_quote(c)})" for c in columns)
    placeholders = ",".join("?" for _ in EMAIL_TYPES_ORDER)
    return tuple(db.execute(f"select {selected},{storage} from main.actions "
                            f"where record_type not in ({placeholders}) order by id",
                            EMAIL_TYPES_ORDER))


def _source_facts(db):
    return {name: (_rows(db, name, _columns(db, name), rowid=name in EMAIL_ROWID_TYPES),
                   _types(db, name, _columns(db, name), rowid=name in EMAIL_ROWID_TYPES))
            for name in SOURCE_TABLES}


def _original_source_rows_unchanged(before, after, fixture_ids):
    for name in SOURCE_TABLES:
        columns = (("rowid",) if name in EMAIL_ROWID_TYPES else ()) + tuple(
            row["name"] for row in before["declarations"][name]["columns"] if row["hidden"] == 0)
        old_rows, old_types = before["facts"][name]
        new_rows, new_types = after[name]
        if "classification_id" in columns:
            position = columns.index("classification_id")
            retained = [(row, types) for row, types in zip(new_rows, new_types, strict=True)
                        if row[position] not in fixture_ids]
            assert retained == list(zip(old_rows, old_types, strict=True)), name
        else:
            assert (new_rows, new_types) == (old_rows, old_types), name


def _original_source_rows_preserved(original, current):
    for name in SOURCE_TABLES:
        old_rows, old_types = original[name]
        new_rows, new_types = current[name]
        assert not (Counter(zip(old_rows, old_types, strict=True)) -
                    Counter(zip(new_rows, new_types, strict=True))), name


def _target_facts(db, variant):
    return {name: _projection(db, name, variant) for name in SOURCE_TABLES}


def _target_current_from_donor(db, before, donor, state, donor_waters):
    updates = []
    for name in SOURCE_TABLES:
        # The source declaration, rather than candidate payload JSON, defines
        # both the projection order and every value used by this fixture.
        columns = (("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name)
        old_rows = {row[0] if name not in EMAIL_COMPOSITE_KEYS else tuple(
            row[columns.index(field)] for field in EMAIL_COMPOSITE_KEYS[name]): row
            for row in before[name][0]}
        new_rows = donor[name][0]
        assert len(new_rows) >= len(old_rows), name
        for row in new_rows:
            identity = (tuple(row[columns.index(field)] for field in EMAIL_COMPOSITE_KEYS[name])
                        if name in EMAIL_COMPOSITE_KEYS else row[0])
            values = dict(zip(columns, row, strict=True))
            if name in EMAIL_ROWID_TYPES:
                values.pop("rowid")
            previous = old_rows.pop(identity, None)
            if previous is None:
                if name in EMAIL_ROWID_TYPES:
                    action_storage.insert_email_action(db, name, values, source_rowid=identity)
                else:
                    action_storage._insert_email_action(db, name, values)
            elif row != previous:
                changes = {column: value for column, value, prior in
                           zip(columns, row, previous, strict=True) if value != prior and column != "rowid"}
                updates.append((name, identity, changes))
        assert not old_rows, name
    for name, identity, changes in updates:
        if name in EMAIL_ROWID_TYPES:
            action_storage.update_email_action(db, name, identity, changes)
        else:
            action_storage._update_email_action(db, name, identity, changes)
    for name, water in donor_waters.items():
        _set_sequence(db, f"actions:{name}", water)
    assert _target_facts(db, state["email_variant"]) == {
        name: ((("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name),
               *donor[name]) for name in SOURCE_TABLES}


def _source_waters(db):
    return {name: _sequence(db, name) for name in EMAIL_AUTOINCREMENT_TYPES}


def _target_waters(db):
    return {name: _sequence(db, f"actions:{name}") for name in EMAIL_AUTOINCREMENT_TYPES}


def _other_sequences(db):
    excluded = (*EMAIL_AUTOINCREMENT_TYPES,
                *(f"actions:{name}" for name in EMAIL_AUTOINCREMENT_TYPES))
    return tuple(db.execute(
        "select name,seq from main.sqlite_sequence where name not in (?,?,?,?) order by name",
        excluded))


OLD_PROGRAM = r'''
import json, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import (EmailStore, email_action_identity,
    email_unsubscribe_effect_digest, email_imap_configuration_snapshot)
from app.email_unsubscribe import extract_unsubscribe_entries
from app.email_classifier_contracts import (EmailAction, EmailCategory, EmailClassification,
    EmailClassificationStatus, build_email_action_plan)
path=Path(sys.argv[1]); mode=sys.argv[2]; store=AutoReplyStore(path); email=EmailStore(path)
if mode == 'seed':
    fixture_account=sys.argv[3] if len(sys.argv)>3 else 'copy-only-account'
    fixture_address=('copy@example.test' if fixture_account=='copy-only-account'
                     else 'baseline@example.test')
    account={'account_id':fixture_account,'display_name':'Copy only',
        'email_address':fixture_address,'imap_host':'imap.example.test',
        'imap_port':993,'imap_tls':True,'imap_username':'copy@example.test',
        'imap_secret_reference':'keychain://copy-only-imap',
        'smtp_host':'smtp.example.test','smtp_port':465,'smtp_tls':True,
        'smtp_username':'copy@example.test','smtp_secret_reference':'keychain://copy-only-smtp',
        'enabled':False,'scan_folders':['INBOX'],'scan_interval_seconds':60}
    with sqlite3.connect(path) as db:
        email._insert_account(db,account,created_at='2026-10-14T00:00:00+00:00',
                              updated_at='2026-10-14T00:00:00+00:00')
    with sqlite3.connect(path) as db:
        classification_id=db.execute('select coalesce(max(id),0)+1000 from email_classifications').fetchone()[0]
    def pending(identifier):
      return EmailClassification.model_validate({
        'classification_id':identifier,
        'stable_message_identity':f'{fixture_account}:message-id:<copy-{identifier}@example.test>',
        'provider_locator':{'account_id':fixture_account,'folder':'INBOX','uidvalidity':42,
                            'uid':identifier,'rfc_message_id':f'<copy-{identifier}@example.test>',
                            'thread_id':'copy-only-thread'},
        'category':'work','confidence':0.79,'margin':0.31,
        'probabilities':{'work':0.79,'legal':0.21},'model_id':'copy-only-model',
        'config_version':'copy-only-config','status':EmailClassificationStatus.PENDING_FEEDBACK,
        'classification_source':'model','action_plan':None})
    persisted=email.persist_scan_result(pending(classification_id),model_text='__subject__offline copy proof')
    assert persisted['id']==classification_id
    receipt_id=classification_id+1
    plan=build_email_action_plan(classification_id=receipt_id,account_id=fixture_account,
        category=EmailCategory.WORK,classification_source='model',confidence=0.79,
        model_id='copy-only-model',config_version='copy-only-config',
        actions=(EmailAction.AUTO_REPLY,EmailAction.MARK_READ,EmailAction.LABEL,
                 EmailAction.UNSUBSCRIBE,EmailAction.TRASH),
        action_parameters={EmailAction.AUTO_REPLY:{'instruction':'Offline copy fixture'},
                           EmailAction.LABEL:{'labels':['copy-only']}},
        created_at=datetime(2026,10,14,tzinfo=timezone.utc))
    receipt=pending(receipt_id).model_copy(update={
        'status':EmailClassificationStatus.PROCESSED,'action_plan':plan})
    assert email.persist_scan_result(receipt,
        model_text='__subject__offline receipt proof')['id']==receipt_id
    retry_classification_id=receipt_id+1
    retry_plan=build_email_action_plan(classification_id=retry_classification_id,
        account_id=fixture_account,category=EmailCategory.WORK,
        classification_source='model',confidence=0.79,model_id='copy-only-model',
        config_version='copy-only-config',actions=(EmailAction.TRASH,),
        action_parameters={},created_at=datetime(2026,10,14,tzinfo=timezone.utc))
    retry_receipt=pending(retry_classification_id).model_copy(update={
        'status':EmailClassificationStatus.PROCESSED,'action_plan':retry_plan})
    assert email.persist_scan_result(retry_receipt,
        model_text='__subject__offline retry proof')['id']==retry_classification_id
    with sqlite3.connect(path) as db:
        direct_ids=dict(db.execute('select action_type,action_id from email_actions '
                                   'where classification_id=?',(receipt_id,)))
        retry_id=db.execute('select action_id from email_actions '
                            'where classification_id=? and action_type=?',
                            (retry_classification_id,'trash')).fetchone()[0]
    print(json.dumps({'classification_id':classification_id,'receipt_classification_id':receipt_id,
                      'retry_classification_id':retry_classification_id,
                      'receipt_plan_id':plan.action_plan_id,
                      'direct_action_id':direct_ids['mark_read'],
                      'failed_action_id':direct_ids['label'],
                      'retry_action_id':retry_id}))
elif mode == 'read':
    classification_id=int(sys.argv[3]); identity=sys.argv[4]
    direct_id=sys.argv[5]; failed_id=sys.argv[6]; retry_id=sys.argv[7]
    assert email.get_classification(classification_id) is not None
    assert email.get_email_reply_receipt(identity)['provider_receipt']['smtp_acceptance_id']
    assert len(email.list_action_attempts(direct_id))==1
    assert len(email.list_action_attempts(failed_id))==1
    assert len(email.list_action_attempts(retry_id))==2
    assert len(email.list_failed_direct_actions(account_id='copy-only-account',
        action_types=(EmailAction.LABEL,),limit=10,action_id=failed_id))==1
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from email_unsubscribe_steps '
                          "where reference='copy-only-step'").fetchone()==(1,)
    print(json.dumps({'read':True}))
elif mode == 'write':
    classification_id=int(sys.argv[3]); identity=sys.argv[4]
    retry_id=sys.argv[5]
    first=email.apply_human_classification(classification_id, EmailCategory.WORK,
        feedback_request_id='copy-only-feedback',expected_current_action_plan_id=None,
        created_at=datetime(2026,10,14,1,4,tzinfo=timezone.utc))
    assert first is not None and first.applied
    second=email.apply_human_classification(classification_id, EmailCategory.WORK,
        feedback_request_id='copy-only-feedback',expected_current_action_plan_id=None,
        created_at=datetime(2026,10,14,1,4,tzinfo=timezone.utc))
    assert second is not None and not second.applied
    receipt=email.get_email_reply_receipt(identity)
    assert receipt['provider_receipt']['smtp_acceptance_id']=='copy-only-smtp'
    assert email.persist_email_reply_receipt(
        action_identity=identity,effect_digest=receipt['effect_digest'],
        action_plan_id=receipt['action_plan_id'],classification_id=receipt['classification_id'],
        account_id=receipt['account_id'],stable_message_identity=receipt['stable_message_identity'],
        outgoing_message_id=receipt['outgoing_message_id'],provider_operation=receipt['provider_operation'],
        provider_target=receipt['provider_target'],provider_result_id=receipt['provider_result_id'],
        sent_folder='Sent',sent_uidvalidity=42,sent_uid=123,
        smtp_acceptance_id='copy-only-smtp')['provider_receipt']['smtp_acceptance_id']=='copy-only-smtp'
    second_retry=email.claim_direct_action(action_id=retry_id,
        claimed_at='2026-10-14T01:05:00+00:00')
    assert second_retry is not None and second_retry.attempt_number==2
    retry_done=email.complete_direct_action_attempt(second_retry,status='done',
        provider_operation='trash',provider_target=second_retry.locator.stable_message_identity,
        provider_result_id='copy-only-retry-done',error='',
        finished_at='2026-10-14T01:05:01+00:00')
    assert retry_done['status']=='done' and len(email.list_action_attempts(retry_id))==2
    print(json.dumps({'feedback_applied':first.applied,'feedback_replayed':not second.applied,
                      'receipt_public_replay':True,'retry_completed':True}))
elif mode == 'current':
    classification_id=int(sys.argv[3]); identity=sys.argv[4]
    direct_id=sys.argv[5]; failed_id=sys.argv[6]; retry_id=sys.argv[7]
    receipt=email.get_email_reply_receipt(identity)
    assert receipt is not None and receipt['provider_receipt']['smtp_acceptance_id']==''
    result=email.persist_email_reply_receipt(
        action_identity=identity,effect_digest=receipt['effect_digest'],
        action_plan_id=receipt['action_plan_id'],classification_id=receipt['classification_id'],
        account_id=receipt['account_id'],stable_message_identity=receipt['stable_message_identity'],
        outgoing_message_id=receipt['outgoing_message_id'],provider_operation=receipt['provider_operation'],
        provider_target=receipt['provider_target'],provider_result_id=receipt['provider_result_id'],
        sent_folder='Sent',sent_uidvalidity=42,sent_uid=123,smtp_acceptance_id='copy-only-smtp')
    assert result['provider_receipt']['smtp_acceptance_id']=='copy-only-smtp'
    claimed=email.claim_direct_action(action_id=direct_id,claimed_at='2026-10-14T01:00:00+00:00')
    assert claimed is not None and claimed.action_id==direct_id
    attempt=email.complete_direct_action_attempt(claimed,status='done',
        provider_operation='mark_read',
        provider_target=claimed.locator.stable_message_identity,
        provider_result_id='copy-only-direct-result',error='',
        finished_at='2026-10-14T01:00:01+00:00')
    assert attempt['status']=='done' and len(email.list_action_attempts(direct_id))==1
    failed_claim=email.claim_direct_action(action_id=failed_id,
        claimed_at='2026-10-14T01:01:00+00:00')
    assert failed_claim is not None
    failed_attempt=email.complete_direct_action_attempt(failed_claim,status='failed',
        provider_operation='label',provider_target=failed_claim.locator.stable_message_identity,
        provider_result_id='',error='copy-only-failure',retryable=False,
        finished_at='2026-10-14T01:01:01+00:00')
    assert failed_attempt['status']=='failed' and failed_attempt['id']>attempt['id']
    failed=email.list_failed_direct_actions(account_id=failed_claim.account_id,
        action_types=(EmailAction.LABEL,),limit=10,action_id=failed_id)
    assert len(failed)==1 and failed[0].action.action_id==failed_id
    entry=extract_unsubscribe_entries(list_unsubscribe='<https://copy.example.test/unsubscribe>')[0]
    binding=dict(action_identity=email_action_identity(account_id='copy-only-account',
        stable_message_identity=claimed.locator.stable_message_identity,
        action_type=EmailAction.UNSUBSCRIBE,action_plan_version=1),
        action_plan_id=claimed.action_plan_id,action_plan_version=1,
        classification_id=claimed.classification_id,account_id=claimed.account_id,
        stable_message_identity=claimed.locator.stable_message_identity,
        thread_identity='copy-only-thread',entry_reference=entry.reference)
    operations=[{'operation_reference':'unsubscribe-operation:open-entry',
                 'kind':'open_entry','target_reference':entry.reference}]
    digest=email_unsubscribe_effect_digest(**binding,operations=operations)
    terminal=email.persist_email_unsubscribe_terminal(**binding,effect_digest=digest,
        operations=operations,outcome='skipped_login_required',
        receipt_id='unsubscribe-receipt:copy-login',evidence='copy-only-terminal-page',
        journal_steps=[{'sequence':1,'operation':'open_entry','state':'done',
                        'reference':'copy-only-step'}])
    assert terminal['outcome']=='skipped_login_required'
    assert email.get_email_unsubscribe_receipt(binding['action_identity'])['outcome']=='skipped_login_required'
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from email_unsubscribe_steps '
                          'where action_identity=?',(binding['action_identity'],)).fetchone()==(1,)
    retry_claim=email.claim_direct_action(action_id=retry_id,
        claimed_at='2026-10-14T01:02:00+00:00')
    assert retry_claim is not None
    first_retry=email.complete_direct_action_attempt(retry_claim,status='failed',
        provider_operation='trash',provider_target=retry_claim.locator.stable_message_identity,
        provider_result_id='',error='copy-only-source-uncertain',retryable=False,
        finished_at='2026-10-14T01:02:01+00:00')
    retry_failed=email.list_failed_direct_actions(account_id=retry_claim.account_id,
        action_types=(EmailAction.TRASH,),limit=10,action_id=retry_id)
    assert len(retry_failed)==1 and retry_failed[0].action.action_id==retry_id
    account_snapshot=email_imap_configuration_snapshot(email.get_account(retry_claim.account_id))
    assert email.release_source_verified_direct_action(retry_failed[0],
        source_locator=retry_failed[0].action.locator,
        source_revision='copy-only-synthetic-revision',
        requested_at='2026-10-14T01:03:00+00:00',apply=True,
        account_configuration_snapshot=account_snapshot)
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from email_direct_retry_requests '
                          'where action_id=? and attempt_number=2',(retry_id,)).fetchone()==(1,)
    assert first_retry['status']=='failed'
    with sqlite3.connect(path) as db:
        public_rowid=db.execute('select rowid from email_actions where action_id=?',
                                (failed_id,)).fetchone()[0]
    history=store.list_operation_logs(source_tables=('email_actions',))
    assert any(item.source_id==public_rowid and item.status=='failed' for item in history)
    assert store is not None
    print(json.dumps({'receipt_read_write':True,'direct_attempt_completed':True,
                      'failed_action_visible':True,'history_source_id_visible':True,
                      'sequence_continued':True,'unsubscribe_skip':True,
                      'explicit_retry_recorded':True}))
'''


def _old_process_genuine(root, path, mode, *args):
    result = subprocess.run([sys.executable, "-c", OLD_PROGRAM, str(path), mode,
                             *(str(arg) for arg in args)], cwd=root,
                            env={**os.environ, "PYTHONPATH": str(root)},
                            text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"published old-code {mode} failed: {result.stderr[-2500:]}")
    return json.loads(result.stdout)


def _old_process(root, path, mode, *args):
    return _old_process_genuine(root, path, mode, *args)


def _copy(source, destination):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, \
            closing(sqlite3.connect(destination)) as copy:
        original.execute("pragma query_only=on")
        original.backup(copy)


def _seed_blank_receipt(db, receipt_id, receipt_plan):
    row = db.execute("select account_id,stable_message_identity from email_classifications where id=?",
                     (receipt_id,)).fetchone()
    assert row is not None
    values = {
        "action_identity":"copy-only-reply","effect_digest":"copy-only-digest",
        "action_plan_id":receipt_plan,"classification_id":receipt_id,
        "account_id":row[0],"stable_message_identity":row[1],
        "outgoing_message_id":"<copy-out@example.test>","provider_operation":"sent_readback",
        "provider_target":"copy-only-target","provider_result_id":"copy-only-result",
        "provider_receipt_json":json.dumps({"sent_folder":"Sent","sent_uidvalidity":42,
            "sent_uid":123,"smtp_acceptance_id":""},sort_keys=True,separators=(",",":")),
        "display_excerpt":"Automatic email reply verified in Sent.",
        "created_at":"2026-10-14T00:00:00+00:00"}
    db.execute("insert into email_reply_receipts(" + ",".join(map(_quote, values)) +
               ") values(" + ",".join("?" for _ in values) + ")", tuple(values.values()))


def _case(path, oldroot, *, fresh, null_plans):
    with closing(sqlite3.connect(path)) as db:
        original_source = capture_email_action_source(db)
        preseed = {
            "declarations": original_source["source_tables"],
            "facts": _source_facts(db),
            "unrelated": _unrelated(db),
            "actions": _actions(db, _columns(db, "actions")),
            "marker": _marker(db), "ledger": _email_ledger(db),
            "waters": _source_waters(db),
            "other_sequences": _other_sequences(db),
            "message_max": db.execute("select max(id) from email_messages").fetchone()[0] or 0,
            "fk": _consistency(db),
        }
        assert db.execute("select count(*) from email_classifications "
                          "where account_id='copy-only-account'").fetchone() == (0,)
    donor_path = path.with_name(path.stem + "-genuine-old-donor.sqlite3")
    assert not donor_path.exists()
    _copy(path, donor_path)
    seed_started = datetime.now(timezone.utc)
    donor_seeded = _old_process_genuine(oldroot, donor_path, "seed")
    with closing(sqlite3.connect(donor_path)) as donor_db:
        _seed_blank_receipt(donor_db, donor_seeded["receipt_classification_id"],
                            donor_seeded["receipt_plan_id"])
        donor_db.commit()
    seeded = _old_process(oldroot, path, "seed")
    seed_finished = datetime.now(timezone.utc)
    assert seeded == donor_seeded
    classification_id = seeded["classification_id"]
    receipt_id = seeded["receipt_classification_id"]
    receipt_plan = seeded["receipt_plan_id"]
    direct_id = seeded["direct_action_id"]
    failed_id = seeded["failed_action_id"]
    retry_id = seeded["retry_action_id"]
    fixture_ids = (classification_id, receipt_id, seeded["retry_classification_id"])
    with closing(sqlite3.connect(path)) as db:
        _seed_blank_receipt(db, receipt_id, receipt_plan)
        db.commit()
        fixture_rows = _fixture_rows(db, fixture_ids)
        with closing(sqlite3.connect(donor_path)) as donor_db:
            assert _normalized_seed_source(db, fixture_ids, seed_started, seed_finished) == (
                _normalized_seed_source(donor_db, fixture_ids, seed_started, seed_finished))
            assert _normalized_seed_fixture(db, fixture_ids, seed_started, seed_finished) == (
                _normalized_seed_fixture(donor_db, fixture_ids, seed_started, seed_finished))
        current_donor_path = path.with_name(path.stem + "-current-donor.sqlite3")
        assert not current_donor_path.exists()
        _copy(path, current_donor_path)
        assert {name: len(rows) for name, rows in fixture_rows.items()} == {
            "email_accounts":1,"email_classifications":3,"email_messages":3}
        assert db.execute("select display_name,email_address,enabled from email_accounts "
                          "where account_id='copy-only-account'").fetchone() == (
                              "Copy only", "copy@example.test", 0)
        prior_message_water = dict(preseed["other_sequences"]).get("email_messages") or 0
        first_message_id = max(prior_message_water, preseed["message_max"]) + 1
        assert tuple(db.execute("select id from email_messages where account_id=? "
                                "and stable_message_identity like ? order by id",
                                ("copy-only-account", "copy-only-account:message-id:<copy-%"))) == tuple(
                                    (first_message_id + offset,) for offset in range(3))
        assert _sequence(db, "email_messages") == first_message_id + 2
        initial_fk = _consistency(db)
        source = capture_email_action_source(db)
        original_marker = _marker(db)
        original_ledger = _email_ledger(db)
        original_unrelated = preseed["unrelated"]
        original_columns = _columns(db, "actions")
        original_actions = _actions(db, original_columns)
        original_email = _source_facts(db)
        _original_source_rows_unchanged(preseed, original_email, fixture_ids)
        postseed_unrelated = _unrelated(db, fixture_ids)
        assert postseed_unrelated == original_unrelated, [
            name for name in original_unrelated
            if postseed_unrelated[name] != original_unrelated[name]]
        assert _actions(db, original_columns) == preseed["actions"]
        assert (_marker(db), _email_ledger(db), _source_waters(db), initial_fk) == (
            preseed["marker"], preseed["ledger"], preseed["waters"], preseed["fk"])
        assert tuple(item for item in _other_sequences(db) if item[0] != "email_messages") == tuple(
            item for item in preseed["other_sequences"] if item[0] != "email_messages")
        source_counts = {name: len(original_email[name][0]) for name in SOURCE_TABLES}
        original_waters = _source_waters(db)
        original_other_sequences = _other_sequences(db)
        initial_tables = db.execute("select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0]
        assert original_marker[0] == "2026-10-13.5" and original_ledger[-1][0] == 51
        assert source["action_variant"] == ("fresh_memory_fk" if fresh else "actual_no_memory_fk")
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_email_action_schema(db)
        db.commit()
        assert _unrelated(db, fixture_ids) == original_unrelated
        assert _fixture_rows(db, fixture_ids) == fixture_rows
        assert _actions(db, original_columns) == original_actions
        assert _target_waters(db) == original_waters
        assert _other_sequences(db) == original_other_sequences
        assert _target_facts(db, state["email_variant"]) == {
            name: ((("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name),
                   *original_email[name]) for name in SOURCE_TABLES}
        _consistency(db, initial_fk)
        assert _marker(db) == original_marker and _email_ledger(db) == original_ledger
        assert db.execute("select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0] == initial_tables - 13
        return _continue_case(db, path, oldroot, state, classification_id, receipt_id, receipt_plan,
                              direct_id, failed_id, retry_id, null_plans, fixture_ids,
                              original_unrelated,
                              original_actions, original_columns, original_marker, original_ledger,
                              initial_fk, initial_tables, source_counts, original_other_sequences,
                              preseed["facts"], original_email, fixture_rows, current_donor_path)


def _columns_from_state(state, name):
    return tuple(item["name"] for item in state["source_tables"][name]["columns"] if item["hidden"] == 0)


def _continue_case(db, path, oldroot, state, classification_id, receipt_id, receipt_plan,
                   direct_id, failed_id, retry_id, null_plans, fixture_ids, unrelated, actions,
                   action_columns, marker, ledger, initial_fk, initial_tables, source_counts,
                   other_sequences, preseed_source_facts, seeded_email, seeded_fixture,
                   donor_path):
    identity = "copy-only-reply"
    raised_waters = {}
    with closing(sqlite3.connect(donor_path)) as donor_db:
        for name, current_water in _target_waters(db).items():
            raised = (current_water or 0) + 17
            assert raised < 2**63
            _set_sequence(db, f"actions:{name}", raised)
            _set_sequence(donor_db, name, raised)
            raised_waters[name] = raised
        donor_db.commit()
    db.commit()
    current_write = _old_process_genuine(oldroot, donor_path, "current", classification_id,
                                         identity, direct_id, failed_id, retry_id)
    assert all(current_write.get(key) is True for key in (
        "receipt_read_write", "direct_attempt_completed", "failed_action_visible",
        "history_source_id_visible", "sequence_continued", "unsubscribe_skip",
        "explicit_retry_recorded"))
    with closing(sqlite3.connect(donor_path)) as donor_db:
        donor_current = _source_facts(donor_db)
        donor_waters = _source_waters(donor_db)
        assert all(donor_waters[name] > raised_waters[name]
                   for name in EMAIL_AUTOINCREMENT_TYPES)
        assert _unrelated(donor_db, fixture_ids) == unrelated
        assert _fixture_rows(donor_db, fixture_ids) == seeded_fixture
        assert _other_sequences(donor_db) == other_sequences
        _original_source_rows_preserved(preseed_source_facts, donor_current)
        assert donor_db.execute("select json_extract(provider_receipt_json,"
                                "'$.smtp_acceptance_id') from email_reply_receipts "
                                "where action_identity='copy-only-reply'").fetchone() == (
                                    "copy-only-smtp",)
        assert donor_db.execute("select outcome from email_unsubscribe_receipts "
                                "where account_id='copy-only-account'").fetchone() == (
                                    "skipped_login_required",)
    assert len(donor_current["email_action_attempts"][0]) == len(
        seeded_email["email_action_attempts"][0]) + 3
    assert len(donor_current["email_unsubscribe_steps"][0]) == len(
        seeded_email["email_unsubscribe_steps"][0]) + 1
    assert len(donor_current["email_unsubscribe_receipts"][0]) == len(
        seeded_email["email_unsubscribe_receipts"][0]) + 1
    assert len(donor_current["email_direct_retry_requests"][0]) == len(
        seeded_email["email_direct_retry_requests"][0]) + 1
    assert donor_current["email_reply_receipts"] != seeded_email["email_reply_receipts"]
    _target_current_from_donor(db, seeded_email, donor_current, state, donor_waters)
    assert _target_facts(db, state["email_variant"]) == {
        name: ((("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name),
               *donor_current[name]) for name in SOURCE_TABLES}
    assert _target_waters(db) == donor_waters
    assert _fixture_rows(db, fixture_ids) == seeded_fixture
    if null_plans:
        row = db.execute("select id,account_id from email_classifications where id=?",
                         (receipt_id,)).fetchone()
        assert row is not None
        for version in (2, 3):
            action_storage.insert_email_action(db, "email_action_plans", {
                "action_plan_id":None,"action_plan_version":version,
                "classification_id":receipt_id,"account_id":row[1],"category":"work",
                "classification_source":"model","confidence":0.79,"model_id":"copy-only-model",
                "config_version":"copy-only-config","actions_json":"[]", "action_parameters_json":"{}",
                "created_at":"2026-10-14T00:00:00+00:00"})
    db.commit()
    current = _target_facts(db, state["email_variant"])
    current_waters = _target_waters(db)
    current_other_sequences = _other_sequences(db)
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    restore_email_action_schema(db, state)
    db.commit()
    assert _source_facts(db) == {name: (current[name][1], current[name][2]) for name in SOURCE_TABLES}
    assert _source_waters(db) == current_waters
    assert _other_sequences(db) == current_other_sequences == other_sequences
    assert _unrelated(db, fixture_ids) == unrelated and _actions(db, action_columns) == actions
    assert _fixture_rows(db, fixture_ids) == seeded_fixture
    assert _marker(db) == marker and _email_ledger(db) == ledger
    _consistency(db, initial_fk)
    if null_plans:
        assert db.execute("select count(distinct rowid) from email_action_plans "
                          "where action_plan_id is null").fetchone() == (2,)
        assert db.execute("select count(distinct action_plan_version) from email_action_plans "
                          "where action_plan_id is null").fetchone() == (2,)
        after_old_unrelated = unrelated
        after_old_actions = actions
        after_old_email = _source_facts(db)
        _original_source_rows_preserved(preseed_source_facts, after_old_email)
        after_old_waters = _source_waters(db)
        after_old_fixture = seeded_fixture
    else:
        feedback_started = datetime.now(timezone.utc)
        _old_process_genuine(oldroot, donor_path, "write", classification_id, identity,
                             retry_id)
        with closing(sqlite3.connect(donor_path)) as donor_db:
            donor_after_write = _source_facts(donor_db)
            donor_after_write_waters = _source_waters(donor_db)
            assert _unrelated(donor_db, fixture_ids) == unrelated
        write = _old_process(oldroot, path, "write", classification_id, identity, retry_id)
        feedback_finished = datetime.now(timezone.utc)
        assert all(write.get(key) is True for key in (
            "feedback_applied", "feedback_replayed", "receipt_public_replay",
            "retry_completed"))
        _old_process(oldroot, path, "read", classification_id, identity,
                     direct_id, failed_id, retry_id)
        after_old_unrelated = _unrelated(db, fixture_ids)
        after_old_actions = _actions(db, action_columns)
        after_old_email = _source_facts(db)
        _original_source_rows_preserved(preseed_source_facts, after_old_email)
        assert _normalized_feedback_source(db, after_old_email, feedback_started,
                                           feedback_finished) == _normalized_feedback_source(
                                               db, donor_after_write, feedback_started,
                                               feedback_finished)
        with closing(sqlite3.connect(donor_path)) as donor_db:
            assert _normalized_feedback_fixture(db, fixture_ids, feedback_started,
                                                feedback_finished) == _normalized_feedback_fixture(
                                                    donor_db, fixture_ids, feedback_started,
                                                    feedback_finished)
        after_old_fixture = _fixture_rows(db, fixture_ids)
        after_old_waters = _source_waters(db)
        assert after_old_waters == donor_after_write_waters
        assert after_old_waters["email_action_attempts"] > current_waters["email_action_attempts"]
        assert after_old_waters["email_unsubscribe_steps"] == current_waters["email_unsubscribe_steps"]
        assert after_old_actions == actions
        assert after_old_unrelated == unrelated
        assert db.execute("select status from email_classifications where id=?",
                          (classification_id,)).fetchone() == ("processed",)
    after_old_other_sequences = _other_sequences(db)
    assert after_old_other_sequences == other_sequences
    _consistency(db, initial_fk)
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    migrate_email_action_schema(db)
    db.commit()
    assert _target_facts(db, state["email_variant"]) == {
        name: ((("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name),
               *after_old_email[name]) for name in SOURCE_TABLES}
    assert _target_waters(db) == after_old_waters
    assert _other_sequences(db) == after_old_other_sequences
    assert _unrelated(db, fixture_ids) == after_old_unrelated
    assert _fixture_rows(db, fixture_ids) == after_old_fixture
    assert _actions(db, action_columns) == after_old_actions
    assert _marker(db) == marker and _email_ledger(db) == ledger
    _consistency(db, initial_fk)
    assert db.execute("select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0] == initial_tables - 13
    return {"variant":state["email_variant"],"initial_tables":initial_tables,
            "target_tables":initial_tables-13,"source_counts_after_fixture_seed":source_counts,
            "baseline_fk_findings":len(initial_fk),"current_fact_inverse":True,
            "old_public_feedback_replay":not null_plans,"old_public_receipt_write":not null_plans,
            "old_public_direct_attempt_completion":not null_plans,
            "old_public_failed_visibility":not null_plans,
            "old_history_source_id":not null_plans,
            "old_public_unsubscribe_skip":not null_plans,
            "old_public_explicit_retry":not null_plans,
            "sql_legal_null_plan_rows":2 if null_plans else 0,
            "reforward_values_types_locators_waters_equal":True}


def run(source, source_ref, repo):
    binding = json.loads(BINDING.read_text())
    assert binding["production_checkout_head_after"] == PUBLISHED_SCHEMA_REF
    production_before = subprocess.check_output(["git", "-C", str(PRODUCTION_ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert production_before == source_ref
    assert subprocess.check_output(["git", "-C", str(repo), "merge-base", PUBLISHED_SCHEMA_REF,
                                    source_ref], text=True).strip() == PUBLISHED_SCHEMA_REF
    candidate_commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    proof_paths = ("scripts/validate_action_email_copy.py", "tests/test_email_action_copy_proof.py",
                   "app/action_storage.py", "app/action_email_storage.py",
                   "app/action_email_definitions.py")
    assert not subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain", "--",
                                        *proof_paths], text=True).strip(), "proof code is uncommitted"
    module_paths = ("app/store.py", "app/email_store.py", "app/action_storage.py",
                    "app/action_email_storage.py", "app/action_email_definitions.py")
    candidate_hashes = {name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
                        for name in module_paths}
    with TemporaryDirectory(prefix="ceo-email13-copy.", dir="/tmp") as temp:
        root = Path(temp)
        oldroot = root / "published-code"
        oldroot.mkdir()
        archive = subprocess.check_output(["git", "-C", str(repo), "archive", source_ref, "app"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(oldroot, filter="data")
        old_hashes = {name: hashlib.sha256((oldroot / name).read_bytes()).hexdigest()
                      for name in module_paths if (oldroot / name).is_file()}
        original = root / "online-backup.sqlite3"
        _copy(source, original)
        with closing(sqlite3.connect(original)) as db:
            db.execute("pragma query_only=on")
            assert _marker(db)[0] == binding["store_marker"]["value"]
            assert _email_ledger(db) == tuple(tuple(item) for item in binding["email_migration_ledger"])
            captured = capture_email_action_source(db)
            assert captured["action_variant"] == binding["actions"]["variant"]
            assert _fingerprint(captured["source_actions"]) == binding["actions"]["declaration_fingerprint_sha256"]
            assert _fingerprint(captured["source_tables"]) == binding["email_declaration_fingerprint_sha256"]
            assert db.execute("select count(*) from sqlite_schema where type='table' "
                              "and name not like 'sqlite_%'").fetchone() == (binding["schema_user_table_count"],)
            _consistency(db)
        actual = root / "actual-work.sqlite3"
        _copy(original, actual)
        actual_case = _case(actual, oldroot, fresh=False, null_plans=False)
        fresh = root / "fresh-work.sqlite3"
        subprocess.run([sys.executable,"-c",
            "from pathlib import Path; import sys; from app.store import AutoReplyStore; from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
            str(fresh)], cwd=oldroot, env={**os.environ,"PYTHONPATH":str(oldroot)},
            text=True, capture_output=True, check=True, timeout=300)
        fresh_sql_case = _case(fresh, oldroot, fresh=True, null_plans=True)
        fresh_application = root / "fresh-application.sqlite3"
        subprocess.run([sys.executable,"-c",
            "from pathlib import Path; import sys; from app.store import AutoReplyStore; from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
            str(fresh_application)], cwd=oldroot, env={**os.environ,"PYTHONPATH":str(oldroot)},
            text=True, capture_output=True, check=True, timeout=300)
        fresh_application_case = _case(fresh_application, oldroot, fresh=True, null_plans=False)
    production_after = subprocess.check_output(["git", "-C", str(PRODUCTION_ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert production_after == production_before
    return {"status":"passed","scope":"offline Email13 copy and genuine published old code",
            "observed_at_utc":datetime.now(timezone.utc).isoformat(),
            "published_source_ref":source_ref,"schema_binding_ref":PUBLISHED_SCHEMA_REF,
            "executed_candidate_commit":candidate_commit,"candidate_module_sha256":candidate_hashes,
            "published_old_module_sha256":old_hashes,
            "production_head_before":production_before,
            "production_head_after":production_after,
            "source_binding":"Non-atomic checkout HEAD and SQLite online backup observations",
            "cases":{"actual_online_backup":actual_case,
                     "published_old_fresh_sql_legal":fresh_sql_case,
                     "published_old_fresh_application":fresh_application_case},
            "checks":{"temporary_copies_cleaned":True,"production_mutations":0,
                      "provider_calls":0,"native_cli_calls":0},
            "limit":"Offline storage only; no constructor activation, native role, CI, publication or live provider acceptance."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.source.resolve(), args.source_ref, Path(__file__).resolve().parents[1])
    args.evidence.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status":result["status"],"cases":list(result["cases"])}))
