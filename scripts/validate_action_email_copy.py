"""Offline Email13 copy proof against the published Store19/Email51 predecessor."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
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
)
from app.action_email_definitions import EMAIL_AUTOINCREMENT_TYPES, EMAIL_ROWID_TYPES, EMAIL_TYPES_ORDER

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


def _digest(db, name):
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
    for values in db.execute(f"select {selected},{storage} from main.{_quote(name)} order by {ordering}"):
        digest.update(pickle.dumps(tuple(values), protocol=4))
        count += 1
    return count, digest.digest()


def _unrelated(db):
    names = [row[0] for row in db.execute(
        "select name from main.sqlite_schema where type='table' and name not like 'sqlite_%' order by name")]
    return {name: _digest(db, name) for name in names
            if name not in (*SOURCE_TABLES, "actions")}


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


def _target_facts(db, variant):
    return {name: _projection(db, name, variant) for name in SOURCE_TABLES}


def _source_waters(db):
    return {name: _sequence(db, name) for name in EMAIL_AUTOINCREMENT_TYPES}


def _target_waters(db):
    return {name: _sequence(db, f"actions:{name}") for name in EMAIL_AUTOINCREMENT_TYPES}


OLD_PROGRAM = r'''
import json, sqlite3, sys
from pathlib import Path
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_classifier_contracts import EmailCategory, EmailClassification, EmailClassificationStatus
path=Path(sys.argv[1]); mode=sys.argv[2]; store=AutoReplyStore(path); email=EmailStore(path)
if mode == 'seed':
    with sqlite3.connect(path) as db:
        classification_id=db.execute('select coalesce(max(id),0)+1000 from email_classifications').fetchone()[0]
    def pending(identifier):
      return EmailClassification.model_validate({
        'classification_id':identifier,
        'stable_message_identity':f'copy-only-account:message-id:<copy-{identifier}@example.test>',
        'provider_locator':{'account_id':'copy-only-account','folder':'INBOX','uidvalidity':42,
                            'uid':identifier,'rfc_message_id':f'<copy-{identifier}@example.test>'},
        'category':'work','confidence':0.79,'margin':0.31,
        'probabilities':{'work':0.79,'legal':0.21},'model_id':'copy-only-model',
        'config_version':'copy-only-config','status':EmailClassificationStatus.PENDING_FEEDBACK,
        'classification_source':'model','action_plan':None})
    persisted=email.persist_scan_result(pending(classification_id),model_text='__subject__offline copy proof')
    assert persisted['id']==classification_id
    receipt_id=classification_id+1
    assert email.persist_scan_result(pending(receipt_id),model_text='__subject__offline receipt proof')['id']==receipt_id
    applied=email.apply_human_classification(receipt_id,EmailCategory.WORK,
        feedback_request_id='copy-only-receipt-feedback',expected_current_action_plan_id=None)
    assert applied is not None and applied.applied
    print(json.dumps({'classification_id':classification_id,'receipt_classification_id':receipt_id,
                      'receipt_plan_id':applied.resulting_action_plan_id}))
elif mode == 'read':
    classification_id=int(sys.argv[3]); identity=sys.argv[4]
    assert email.get_classification(classification_id) is not None
    assert email.get_email_reply_receipt(identity)['provider_receipt']['smtp_acceptance_id']
    print(json.dumps({'read':True}))
elif mode == 'write':
    classification_id=int(sys.argv[3]); identity=sys.argv[4]
    first=email.apply_human_classification(classification_id, EmailCategory.WORK,
        feedback_request_id='copy-only-feedback',expected_current_action_plan_id=None)
    assert first is not None and first.applied
    second=email.apply_human_classification(classification_id, EmailCategory.WORK,
        feedback_request_id='copy-only-feedback',expected_current_action_plan_id=None)
    assert second is not None and not second.applied
    receipt=email.get_email_reply_receipt(identity)
    assert receipt is not None and receipt['provider_receipt']['smtp_acceptance_id']
    result=email.persist_email_reply_receipt(
        action_identity=identity,effect_digest=receipt['effect_digest'],
        action_plan_id=receipt['action_plan_id'],classification_id=receipt['classification_id'],
        account_id=receipt['account_id'],stable_message_identity=receipt['stable_message_identity'],
        outgoing_message_id=receipt['outgoing_message_id'],provider_operation=receipt['provider_operation'],
        provider_target=receipt['provider_target'],provider_result_id=receipt['provider_result_id'],
        sent_folder='Sent',sent_uidvalidity=42,sent_uid=123,smtp_acceptance_id='copy-only-smtp')
    assert result['provider_receipt']['smtp_acceptance_id']=='copy-only-smtp'
    assert store is not None
    print(json.dumps({'feedback_applied':first.applied,'feedback_replayed':not second.applied,
                      'receipt_read_write':True}))
'''


def _old_process(root, path, mode, *args):
    result = subprocess.run([sys.executable, "-c", OLD_PROGRAM, str(path), mode,
                             *(str(arg) for arg in args)], cwd=root,
                            env={**os.environ, "PYTHONPATH": str(root)},
                            text=True, capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"published old-code {mode} failed: {result.stderr[-2500:]}")
    return json.loads(result.stdout)


def _copy(source, destination):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, \
            closing(sqlite3.connect(destination)) as copy:
        original.execute("pragma query_only=on")
        original.backup(copy)


def _case(path, oldroot, *, fresh, null_plans):
    seeded = _old_process(oldroot, path, "seed")
    classification_id = seeded["classification_id"]
    receipt_id = seeded["receipt_classification_id"]
    receipt_plan = seeded["receipt_plan_id"]
    with closing(sqlite3.connect(path)) as db:
        initial_fk = _consistency(db)
        source = capture_email_action_source(db)
        original_marker = _marker(db)
        original_ledger = _email_ledger(db)
        original_unrelated = _unrelated(db)
        original_columns = _columns(db, "actions")
        original_actions = _actions(db, original_columns)
        original_email = _source_facts(db)
        source_counts = {name: len(original_email[name][0]) for name in SOURCE_TABLES}
        original_waters = _source_waters(db)
        initial_tables = db.execute("select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0]
        assert original_marker[0] == "2026-10-13.5" and original_ledger[-1][0] == 51
        assert source["action_variant"] == ("fresh_memory_fk" if fresh else "actual_no_memory_fk")
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        state = migrate_email_action_schema(db)
        db.commit()
        assert _unrelated(db) == original_unrelated
        assert _actions(db, original_columns) == original_actions
        assert _target_waters(db) == original_waters
        assert _target_facts(db, state["email_variant"]) == {
            name: ((("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name),
                   *original_email[name]) for name in SOURCE_TABLES}
        _consistency(db, initial_fk)
        assert _marker(db) == original_marker and _email_ledger(db) == original_ledger
        assert db.execute("select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0] == initial_tables - 13
        return _continue_case(db, path, oldroot, state, classification_id, receipt_id, receipt_plan,
                              null_plans,
                              original_unrelated,
                              original_actions, original_columns, original_marker, original_ledger,
                              initial_fk, initial_tables, source_counts)


def _columns_from_state(state, name):
    return tuple(item["name"] for item in state["source_tables"][name]["columns"] if item["hidden"] == 0)


def _continue_case(db, path, oldroot, state, classification_id, receipt_id, receipt_plan,
                   null_plans, unrelated, actions,
                   action_columns, marker, ledger, initial_fk, initial_tables, source_counts):
    # This fixture exists only in the owned working copy. It is valid Sent evidence
    # with an initially empty SMTP acceptance ID, later enriched by old public code.
    row = db.execute("select id,account_id,stable_message_identity from email_classifications where id=?",
                     (receipt_id,)).fetchone()
    assert row is not None
    identity = "copy-only-reply"
    action_storage.insert_email_action(db, "email_reply_receipts", {
        "action_identity":identity,"effect_digest":"copy-only-digest",
        "action_plan_id":receipt_plan,"classification_id":receipt_id,
        "account_id":row[1],"stable_message_identity":row[2],
        "outgoing_message_id":"<copy-out@example.test>","provider_operation":"sent_readback",
        "provider_target":"copy-only-target","provider_result_id":"copy-only-result",
        "provider_receipt_json":json.dumps({"sent_folder":"Sent","sent_uidvalidity":42,
            "sent_uid":123,"smtp_acceptance_id":""},separators=(",",":")),
        "display_excerpt":"Automatic email reply verified in Sent.",
        "created_at":"2026-10-14T00:00:00+00:00"})
    if null_plans:
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
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    restore_email_action_schema(db, state)
    db.commit()
    assert _source_facts(db) == {name: (current[name][1], current[name][2]) for name in SOURCE_TABLES}
    assert _source_waters(db) == current_waters
    assert _unrelated(db) == unrelated and _actions(db, action_columns) == actions
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
        after_old_waters = _source_waters(db)
    else:
        write = _old_process(oldroot, path, "write", classification_id, identity)
        assert write["feedback_applied"] and write["feedback_replayed"] and write["receipt_read_write"]
        _old_process(oldroot, path, "read", classification_id, identity)
        after_old_unrelated = _unrelated(db)
        after_old_actions = _actions(db, action_columns)
        after_old_email = _source_facts(db)
        after_old_waters = _source_waters(db)
        assert after_old_actions == actions
        assert after_old_unrelated != unrelated  # public feedback updates the owned classification fixture
    _consistency(db, initial_fk)
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    migrate_email_action_schema(db)
    db.commit()
    assert _target_facts(db, state["email_variant"]) == {
        name: ((("rowid",) if name in EMAIL_ROWID_TYPES else ()) + _columns_from_state(state, name),
               *after_old_email[name]) for name in SOURCE_TABLES}
    assert _target_waters(db) == after_old_waters
    assert _unrelated(db) == after_old_unrelated
    assert _actions(db, action_columns) == after_old_actions
    assert _marker(db) == marker and _email_ledger(db) == ledger
    _consistency(db, initial_fk)
    assert db.execute("select count(*) from sqlite_schema where type='table' and name not like 'sqlite_%'").fetchone()[0] == initial_tables - 13
    return {"variant":state["email_variant"],"initial_tables":initial_tables,
            "target_tables":initial_tables-13,"source_counts":source_counts,
            "baseline_fk_findings":len(initial_fk),"current_fact_inverse":True,
            "old_public_feedback_replay":not null_plans,"old_public_receipt_write":not null_plans,
            "sql_legal_null_plan_rows":2 if null_plans else 0,
            "reforward_values_types_locators_waters_equal":True}


def run(source, source_ref, repo):
    binding = json.loads(BINDING.read_text())
    assert binding["production_checkout_head_after"] == PUBLISHED_SCHEMA_REF
    production_before = subprocess.check_output(["git", "-C", str(PRODUCTION_ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert production_before == source_ref
    assert subprocess.check_output(["git", "-C", str(repo), "merge-base", PUBLISHED_SCHEMA_REF,
                                    source_ref], text=True).strip() == PUBLISHED_SCHEMA_REF
    with TemporaryDirectory(prefix="ceo-email13-copy.", dir="/tmp") as temp:
        root = Path(temp)
        oldroot = root / "published-code"
        oldroot.mkdir()
        archive = subprocess.check_output(["git", "-C", str(repo), "archive", source_ref, "app"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(oldroot, filter="data")
        original = root / "online-backup.sqlite3"
        _copy(source, original)
        with closing(sqlite3.connect(original)) as db:
            db.execute("pragma query_only=on")
            assert _marker(db)[0] == binding["store_marker"]["value"]
            assert _email_ledger(db) == tuple(tuple(item) for item in binding["email_migration_ledger"])
            assert capture_email_action_source(db)["action_variant"] == binding["actions"]["variant"]
            _consistency(db)
        actual = root / "actual-work.sqlite3"
        _copy(original, actual)
        actual_case = _case(actual, oldroot, fresh=False, null_plans=False)
        fresh = root / "fresh-work.sqlite3"
        subprocess.run([sys.executable,"-c",
            "from pathlib import Path; import sys; from app.store import AutoReplyStore; from app.email_store import EmailStore; p=Path(sys.argv[1]); AutoReplyStore(p); EmailStore(p)",
            str(fresh)], cwd=oldroot, env={**os.environ,"PYTHONPATH":str(oldroot)},
            text=True, capture_output=True, check=True, timeout=300)
        fresh_case = _case(fresh, oldroot, fresh=True, null_plans=True)
    production_after = subprocess.check_output(["git", "-C", str(PRODUCTION_ROOT), "rev-parse", "HEAD"], text=True).strip()
    assert production_after == production_before
    return {"status":"passed","scope":"offline Email13 copy and genuine published old code",
            "observed_at_utc":datetime.now(timezone.utc).isoformat(),
            "published_source_ref":source_ref,"schema_binding_ref":PUBLISHED_SCHEMA_REF,
            "production_head_before":production_before,
            "production_head_after":production_after,
            "source_binding":"Non-atomic checkout HEAD and SQLite online backup observations",
            "cases":{"actual_online_backup":actual_case,"published_old_fresh_constructor":fresh_case},
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
