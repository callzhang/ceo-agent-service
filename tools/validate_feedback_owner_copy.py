"""Validate Feedback ownership on current data, with public APIs and provider doubles."""
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from app.feedback_owner_schema_release import prepare_feedback_owner_release
from tools import native_copy_validation as native


def fact_digest(path, *, original_ceilings=None):
    facts=native.fact_digest(path,original_ceilings=original_ceilings)
    digest=hashlib.sha256()
    count=0
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        where=' where task_id<=?' if original_ceilings is not None else ''
        args=(original_ceilings['feedback_processing_items'],) if original_ceilings is not None else ()
        for row in db.execute('select task_id,feedback_key,batch_id,current_round_id from feedback_processing_items'+where+' order by task_id',args):
            digest.update(json.dumps([native._scalar(v) for v in row],ensure_ascii=False,separators=(',',':')).encode())
            digest.update(b'\n')
            count+=1
    facts['feedback_processing_items']={'rows':count,'sha256':digest.hexdigest()}
    return facts


PROGRAM=r'''
import json,socket,subprocess,sys
from pathlib import Path
from uuid import uuid4
def refused(*args,**kwargs):
    raise AssertionError('Feedback acceptance attempted network or CLI')
socket.socket.connect=refused
socket.socket.connect_ex=refused
import croniter
subprocess.Popen.__init__=refused
from app.store import AutoReplyStore
from app.feedback_processing import ResolutionEvidence
store=AutoReplyStore(Path(sys.argv[1]))
for prior in json.loads(sys.argv[2]):
    current=store.get_feedback_processing_item(prior['key'])
    assert current.current_round_id==prior['round_id']
    assert current.status=='resolved' and current.resolved_at
    assert current.note=='second-round'
key='feedback-copy:'+str(uuid4())
store.upsert_feedback_event(key=key,feedback_token='copy-token',comment='Synthetic copy acceptance',original_text='Synthetic source')
receipt=ResolutionEvidence(commit_sha='b'*40,test_evidence={'pytest':{'exit_code':0}},
 restart_evidence={'launchd_label':'com.ceo-agent-service.main','before_pid':10,'after_pid':11},
 health_evidence={'url':'http://127.0.0.1:8765/healthz','status_code':200,'ok':True},
 backlog_evidence={'processing':0,'failed':0,'retryable':0})
old_id=None
for ordinal,note in [(1,'first-round'),(2,'second-round')]:
    batch=key+':'+str(ordinal)
    store.create_feedback_processing_batch([key],batch_id=batch)
    claimed=store.claim_feedback_processing_items(batch,[key]);assert len(claimed)==1
    store.associate_feedback_processing_turn(key,workbench_task_id='copy-task',workbench_turn_id='copy-turn',attempt_id=71,agent_run_id=72)
    store.patch_feedback_processing_item_evidence(key,commit_sha=receipt.commit_sha,test_evidence=receipt.test_evidence,restart_evidence=receipt.restart_evidence,health_evidence=receipt.health_evidence,note=note)
    assert store.resolve_feedback_processing_batch(batch,receipt,commit_is_ancestor=True)
    current=store.get_feedback_processing_item(key)
    assert current.status=='resolved' and current.note==note and current.resolved_at
    if ordinal==1:
        old_id=current.current_round_id
        store.reopen_feedback_processing_item(key,reason='Synthetic new round')
        reopened=store.get_feedback_processing_item(key)
        assert reopened.current_round_id==0 and reopened.status=='pending' and reopened.note=='' and reopened.resolved_at==''
    else:
        assert current.current_round_id!=old_id
print(json.dumps({'key':key,'round_id':current.current_round_id,'old_round_id':old_id}))
'''


def _feedback_seed(root,path,expected):
    env={k:os.environ[k] for k in ('PATH','HOME','USER','LANG','LC_ALL','TMPDIR') if k in os.environ}
    env.update(PYTHONPATH=str(root),CEO_WORKER_DB=str(path),CEO_SERVICE_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1',CEO_DATABASE_BACKUP_PAUSED='1')
    run=subprocess.run([sys.executable,'-c',PROGRAM,str(path),json.dumps(expected)],cwd=root,env=env,capture_output=True,text=True,timeout=120)
    if run.returncode:
        raise RuntimeError('cold Feedback fixture failed: '+run.stderr[-4000:])
    return json.loads(run.stdout.splitlines()[-1])


def main():
    parser=argparse.ArgumentParser()
    for name in ('source','copy','old-root','report'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    source,copy,old=args.source.resolve(),args.copy.resolve(),args.old_root.resolve()
    current=Path(__file__).resolve().parents[1]
    native._copy_source(source,copy)
    before=fact_digest(copy)
    ceilings=native._ceilings(copy)
    original=fact_digest(copy,original_ceilings=ceilings)
    release=prepare_feedback_owner_release(copy)
    release.publish()
    release.verify_loaded()
    assert fact_digest(copy)==before
    first=_feedback_seed(current,copy,[])
    present=fact_digest(copy)
    release.rollback()
    assert fact_digest(copy)==present
    second=_feedback_seed(old,copy,[first])
    after_old=fact_digest(copy)
    again=prepare_feedback_owner_release(copy)
    again.publish()
    again.finalize()
    assert fact_digest(copy)==after_old
    third=_feedback_seed(current,copy,[first,second])
    assert fact_digest(copy,original_ceilings=ceilings)==original
    report={'status':'passed','checked_at':datetime.now(timezone.utc).isoformat(),
            'source':str(source),'copy':str(copy),'old_root':str(old),
            'native_tables_including_sequence':len(before),'source_fact_hashes':before,
            'forward_preserved':True,'inverse_preserved_new_rounds_results_receipts':True,
            'actual_old_code_cold_read_write':True,'reforward_preserved':True,
            'original_rows_unchanged':True,'claim_bind_patch_resolve_reopen_next_round':'passed in current,old,current cold processes',
            'feedback_fixtures':[first,second,third],'external_network_and_cli':'refused'}
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_fact_hashes','feedback_fixtures')}))


if __name__=='__main__':
    main()
