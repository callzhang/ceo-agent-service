"""Current-data Email field roundtrip with actual old-code cold reads/writes."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3

from app.email_field_retirement_release import prepare_email_field_retirement_release
from app.oa_history_normalization import normalize_oa_history
from app.task8_storage import register_task8_functions
from tools import native_copy_validation as native
from tools import oa_history_copy_validation as oa


def _new_current_oa(path):
    from app.store import AutoReplyStore
    from app.action_entity_storage import create_action, append_action_receipt
    from app.action_storage import update_action
    from app.reply_receipt_reference import add_reference
    from app.oa_history_storage import record_current_oa_facts
    store = AutoReplyStore(path)
    raw = '{"success":true}'
    attempt_id = store.record_reply_attempt(
        conversation_id='copy-oa', conversation_title='Synthetic OA',
        trigger_message_id='copy-oa-current', trigger_sender='fixture', trigger_text='Synthetic',
        action='oa_approval', sensitivity_kind='normal', oa_action='approve',
        oa_process_instance_id='copy-oa-process', oa_task_id='copy-oa-task',
        send_status='closed', oa_action_result_json=raw,
    )
    with store._connect() as db:
        action = create_action(db, values={'operation': 'approve'})
        receipt = append_action_receipt(db, action['id'], values={'provider_result_json': raw})
        add_reference(db, attempt_id=attempt_id, receipt_id=receipt, expected_body=raw)
        record_current_oa_facts(db, attempt_id=attempt_id, receipt_id=receipt, expected_body=raw,
                               process_instance_id='copy-oa-process', task_id='copy-oa-task')
        update_action(db, 'reply_attempts', attempt_id, {'oa_action_result_json': ''})
    return attempt_id


def _check_current_oa(path, attempt_id):
    from app.approval_history import resolve_approval_history_result
    from app.oa_history_normalization import load_oa_history_source
    from app.oa_history_storage import list_recorded_oa_results
    with sqlite3.connect(path) as db:
        register_task8_functions(db)
        attempts, _ = load_oa_history_source(db)
        attempt = next(a for a in attempts if a.id == attempt_id)
        facts = list_recorded_oa_results(db, [attempt_id])
        assert str(resolve_approval_history_result(attempt, [], recorded_actions=facts)) == 'unknown'
        assert len(facts) == 1 and facts[0]['status'] is None
        assert attempt.oa_action_result_json == '{"success":true}'


PROGRAM = r'''
import json,socket,subprocess,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4
def refused(*args,**kwargs):
    raise AssertionError('Copy acceptance attempted network or native CLI')
socket.socket.connect=refused
socket.socket.connect_ex=refused
import croniter
subprocess.Popen.__init__=refused
from app.store import AutoReplyStore
from app.email_store import EmailStore
from app.email_task_adapter import EmailClassificationTaskAdapter,EmailClassificationTaskInput
from app.email_classifier_contracts import build_versioned_email_action_plan
from app.email_canonical_storage import insert_email_message,insert_email_classification
from app.action_entity_storage import create_action,append_action_receipt
store=AutoReplyStore(Path(sys.argv[1]));email=EmailStore(store.path,validate_rows=False)
prior=json.loads(sys.argv[2])
with email._connect() as db:
    assert db.execute("select count(*) from tasks where kind='email_classification' and status='pending'").fetchone()[0]==0
    leases=[datetime.fromisoformat(row[0]) for row in db.execute(
        "select lease_expires_at from tasks where kind='email_classification' and status='running'")]
clock=min(leases)-timedelta(seconds=1) if leases else datetime.now(timezone.utc)
adapter=EmailClassificationTaskAdapter(email,now=lambda:clock)
for item in prior:
    done=adapter.get_task(item['task_id'])
    assert done.status=='done' and json.loads(done.result_json)=={'decision_status':'processed'}
    record=email.get_classification(item['classification_id'])
    assert record['current_action_plan_id']==item['plan_id']
    assert record['category']=='work'
    with email._connect() as db:
        receipt=db.execute('select provider_result_json from action_receipts where id=?',(item['receipt_id'],)).fetchone()
        assert receipt[0]==item['receipt_body']
account=email.list_accounts()[0]['account_id']
token=uuid4().hex;now=datetime.now(timezone.utc);stamp=now.isoformat()
message_id='<copy-'+token+'@example.invalid>';stable=account+':message-id:'+message_id
message={'account_id':account,'stable_message_identity':stable,'folder':'INBOX','uidvalidity':42,
         'uid':int(now.timestamp()),'rfc_message_id':message_id,'in_reply_to':'','references_json':'[]',
         'thread_identity':stable,'sender':'synthetic@example.invalid','recipients_json':'[]',
         'subject':'Synthetic copy acceptance','normalized_text':'Synthetic copy body','preview':'Synthetic copy body',
         'attachment_metadata_json':'[]','received_at':stamp,'created_at':stamp,'updated_at':stamp}
with email._connect() as db:
    db.execute('begin immediate')
    insert_email_message(db,message)
    values={'account_id':account,'stable_message_identity':stable,'folder':'INBOX','uidvalidity':42,
            'uid':message['uid'],'rfc_message_id':message_id,'thread_id':stable,'sender':message['sender'],
            'subject':message['subject'],'preview':message['preview'],'model_text':message['normalized_text'],
            'received_at':stamp,'category':None,'predicted_category':None,'confirmed_category':None,
            'confidence':1.0,'margin':1.0,'probabilities_json':'{}','model_id':'copy-model',
            'config_version':'copy-config','status':'pending_feedback','classification_source':'agent',
            'agent_result_json':'null','current_action_plan_id':None,'included_in_model_id':None,
            'confirmed_at':'','created_at':stamp,'updated_at':stamp}
    classification_id=insert_email_classification(db,values)
    plan=build_versioned_email_action_plan(action_plan_version=1,classification_id=classification_id,
         account_id=account,category='work',classification_source='agent',confidence=1.0,
         model_id='copy-model',config_version='copy-config',actions=(),action_parameters={},
         action_authorizations=(),created_at=now)
    email._persist_action_plan(db,plan,now=stamp)
    db.execute("update emails set class_category='work',class_status='processed',class_current_action_plan_id=? where classification_id=?",
               (plan.action_plan_id,classification_id))
    action=create_action(db,values={'operation':'copy-receipt','status':'done','created_at':stamp,'updated_at':stamp})
    body=json.dumps({'synthetic_ack':token},sort_keys=True)
    receipt=append_action_receipt(db,action['id'],values={'operation':'copy-receipt','status':'done',
         'provider_result_json':body,'created_at':stamp,'updated_at':stamp})
task_input=EmailClassificationTaskInput.from_message({'accountId':account,'stableMessageIdentity':stable,
    'folder':'INBOX','uidValidity':42,'uid':message['uid'],'messageId':message_id,'providerUnread':True},
    allowed_category_keys=('work',),category_descriptions={'work':{'core':'Synthetic business'}},
    folder_targets={'work':'Work'},config_version='copy-config',unsubscribe_candidates=())
queued=adapter.ensure_task(task_input)
claimed=adapter.claim_next(owner='copy-worker')
assert claimed is not None and claimed.task_id==queued.task_id
adapter.complete(claimed,{'decision_status':'processed'})
assert adapter.ensure_task(task_input).task_id==queued.task_id
EmailStore(store.path,validate_rows=False)
print(json.dumps({'task_id':queued.task_id,'classification_id':classification_id,'plan_id':plan.action_plan_id,
                  'receipt_id':receipt,'receipt_body':body}))
'''


def _cold_write(root, path, prior):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    env.update(PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1", CEO_WORKER_DB=str(path),
               CEO_SERVICE_ROOT=str(root), CEO_DATABASE_BACKUP_PAUSED="1")
    result = subprocess.run([sys.executable, "-c", PROGRAM, str(path), json.dumps(prior)],
                            cwd=root, env=env, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError("Cold Email write failed: " + result.stderr[-5000:])
    return json.loads(result.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    for name in ("source", "copy", "old-root", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    source, copy, old = args.source.resolve(), args.copy.resolve(), args.old_root.resolve()
    root = Path(__file__).resolve().parents[1]
    native._copy_source(source, copy)
    before = native.fact_digest(copy)
    with sqlite3.connect(copy) as db:
        register_task8_functions(db)
        db.execute('begin immediate')
        oa_before = oa.capture(db, normalized=False)
        normalization = normalize_oa_history(db)
        oa_after = oa.capture(db, normalized=True)
        assert oa_before == oa_after, 'OA outcomes, quality predicate or exact bodies changed'
        repeated = normalize_oa_history(db)
        assert repeated['facts'] == repeated['bodies'] == 0, 'OA normalization is not idempotent'
    normalized_source = native.fact_digest(copy)
    for table in ('tasks', 'runs', 'runtime_attempts', 'run_adopted_results', 'run_adopted_reviews'):
        assert before[table] == normalized_source[table], 'OA normalization created or changed execution proof'
    identities = native._identities(copy)
    original = native.fact_digest(copy, original_identities=identities)
    release = prepare_email_field_retirement_release(copy)
    release.publish()
    release.verify_loaded()
    current_oa = _new_current_oa(copy)
    _check_current_oa(copy, current_oa)
    first = _cold_write(root, copy, [])
    target = native.fact_digest(copy)
    release.rollback()
    inverse = native.fact_digest(copy)
    assert native.fact_digest(copy, original_identities=identities) == original
    with sqlite3.connect(copy) as db:
        register_task8_functions(db)
        for normalized in (False, True):
            observed = oa.capture(db, normalized=normalized)
            for key, population in oa_before.items():
                assert {identity: observed[key][identity] for identity in population} == population
    _check_current_oa(copy, current_oa)
    second = _cold_write(old, copy, [first])
    after_old = native.fact_digest(copy)
    again = prepare_email_field_retirement_release(copy)
    again.publish()
    again.verify_loaded()
    _check_current_oa(copy, current_oa)
    again.rollback()
    assert native.fact_digest(copy) == after_old
    final = prepare_email_field_retirement_release(copy)
    final.publish()
    final.finalize()
    _check_current_oa(copy, current_oa)
    third = _cold_write(root, copy, [first, second])
    report = {
        "status": "passed", "checked_at": datetime.now(timezone.utc).isoformat(),
        "native_tables_including_sequence": len(before), "source_fact_hashes": before,
        "normalized_source_fact_hashes": normalized_source,
        "oa_normalization": normalization,
        "oa_semantics": oa.summary(oa_before),
        "oa_attempt_group_quality_and_exact_body_equivalence": True,
        "oa_normalization_idempotent_and_no_execution_proof_changes": True,
        "new_current_unknown_oa_stays_unknown_across_inverse_reforward": True,
        "new_target_fact_hashes": target, "first_inverse_fact_hashes": inverse,
        "old_cold_write_fact_hashes": after_old,
        "normalized_original_cohort_typed_facts_preserved": True,
        "new_target_inputs_plans_results_and_fake_receipts_survive_inverse": True,
        "actual_old_code_cold_read_write": True, "second_inverse_exact_including_new_old_writes": True,
        "email_constructor_validation": "validate_rows=False, matching actual Email worker; schema/account validation retained",
        "fixture_clock": "before existing classification lease expiry; original in-flight work is not recovered or consumed",
        "current_old_current_cold_writes": [first, second, third],
        "network_and_native_cli": "refused", "copy": str(copy), "old_root": str(old),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {"source_fact_hashes", "new_target_fact_hashes", "first_inverse_fact_hashes",
                                     "old_cold_write_fact_hashes", "normalized_source_fact_hashes",
                                     "current_old_current_cold_writes"}}))


if __name__ == "__main__":
    main()
