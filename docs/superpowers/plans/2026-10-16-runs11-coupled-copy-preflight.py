"""Full paired source116/current51/inverse116/current51 in disposable copies.

Run against an explicitly supplied candidate tree. Source reads use SQLite backup;
all public writes, constructors and historical predecessor processes use one
TemporaryDirectory. Digests retain scalar types and native source identities.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import runpy
import sqlite3
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import time

SOURCE_SHA = '970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320'
NAMES = ('runs', 'codex_session_search_index', 'email_classifier_runtime_samples', 'setup_wizard_events')


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def facts(path, *, current, original_bounds=None):
    from app.runs11_definitions import RUN11_BASE_COLUMNS, RUN11_FIELDS, RUN11_NEW_KINDS, run11_projection
    result = {}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        for name in NAMES:
            fields = RUN11_BASE_COLUMNS if name == 'runs' else RUN11_FIELDS[name]
            columns = ','.join('"'+field+'"' for field in fields)
            typed = ','.join('typeof("'+field+'")' for field in fields)
            if current:
                relation = ('select * from main.runs where record_type not in ('+
                            ','.join(map(repr, RUN11_NEW_KINDS))+')') if name == 'runs' else run11_projection(name)
            else:
                relation = 'select * from main."'+name+'"'
            where, params = (' where id<=?', (original_bounds[name],)) if original_bounds else ('', ())
            digest, count = hashlib.sha256(), 0
            for row in db.execute('select '+columns+','+typed+' from ('+relation+')'+where+' order by id', params):
                digest.update(pickle.dumps(tuple(row), protocol=4))
                count += 1
            result[name] = [count, digest.hexdigest()]
    return result


NATIVE_RUN_WRITE = '''
from pathlib import Path
import sqlite3,sys,json
from app.store import AutoReplyStore
from app.run_storage import insert_run
p=Path(sys.argv[1]); s=AutoReplyStore(p)
with s._connect() as db:
 r=insert_run(db,'agent_runtime_attempts',dict(workload_kind='other',workload_key='runs11-coupled-native',attempt_number=1,route_name='copy',runtime_kind='copy',credential_mode='copy',model='copy',status='completed'))
 print(json.dumps({'native_old8_id':r.lastrowid}))
'''
PREDECESSOR_WRITE = '''
from pathlib import Path
import sys,json
from app.store import AutoReplyStore
from app.email_store import EmailStore
p=Path(sys.argv[1]);s=AutoReplyStore(p);EmailStore(p)
s.upsert_codex_session_search_index(session_id='runs11-coupled-predecessor',source_type='copy-only',source_id='predecessor',title='runs11predecessortitle')
j=s.record_setup_wizard_event(step_id='runs11-coupled-predecessor',action_id='copy',status='completed',summary='predecessor')
with s._connect() as db:
 c=db.execute("insert into email_classifier_runtime_samples(model_id,outcome,cache_hit,runtime_warm,queue_ms,http_ms,embedding_ms,head_ms,total_ms,recorded_at) values('runs11-coupled-predecessor','success',0,1,1,2,3,4,10,'copy-only-time')")
 print(json.dumps({'setup_public_id':j,'sample_public_id':c.lastrowid}))
'''


def inside(root, source, production, receipt_path):
    sys.path.insert(0, str(root))
    helpers = runpy.run_path(str(root/'scripts/validate_task20_runtime_copy.py'))
    inventory, run, old_cold = helpers['inventory'], helpers['run'], helpers['COLD']
    wechat_facts = helpers['wechat_facts']
    from app.store_schema_release import prepare_database_schema_release
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.runs11_storage import insert_run11_fact
    from app.run_storage import insert_run
    start = time.monotonic()
    checker_sha = file_hash(Path(__file__))
    assert file_hash(source) == SOURCE_SHA
    predecessor = run(['git','rev-parse','HEAD'], root=production)
    assert predecessor == '44ee956d10a1360c1ba9b44bf08f4c61e630beb4'
    with TemporaryDirectory(prefix='runs11-coupled-copy-', dir='/tmp') as directory:
        owned = Path(directory)
        copy, old_root = owned/'copy.sqlite3', owned/'predecessor'
        old_root.mkdir()
        environment = os.environ | {'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root),
            'CEO_WORKER_DB':str(copy),'CEO_ENV_FILE':str(owned/'absent.env'),
            'CEO_SERVICE_ROOT':str(owned/'absent-service')}
        os.environ.update({name:environment[name] for name in ('CEO_WORKER_DB','CEO_ENV_FILE','CEO_SERVICE_ROOT')})
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro&immutable=1',uri=True)) as native:
            with closing(sqlite3.connect(copy)) as destination:
                native.backup(destination)
        before = inventory(copy)
        assert len(before['ordinary_tables']) == 116
        with closing(sqlite3.connect(copy)) as db:
            bounds = {name:db.execute('select coalesce(max(id),0) from "'+name+'"').fetchone()[0] for name in NAMES}
        original_facts = facts(copy, current=False)
        archive = subprocess.run(['git','archive',predecessor,'app','data/config',
            'docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json'],
            cwd=production,check=True,capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(old_root,filter='data')
        old_env = environment | {'PYTHONPATH':str(old_root)}
        release = prepare_database_schema_release(root,copy)
        assert release is not None and release.source_schema['runs11'] == {'variant':'actual'}
        native_run = json.loads(run([sys.executable,'-B','-c',NATIVE_RUN_WRITE,str(copy)],root=old_root,env=old_env))
        native_wechat = 'from pathlib import Path\nimport sys\nfrom app.store import AutoReplyStore\ns=AutoReplyStore(Path(sys.argv[1]))\nj=s.begin_wechat_memory_import_job(import_run_id="tasks20-copy-native",account_id="copy-only")\nassert j==1\ns.finish_wechat_memory_import_job(j,status="completed",error="copy-only-native")\n'
        run([sys.executable,'-B','-c',native_wechat,str(copy)],root=old_root,env=old_env)
        print(json.dumps({'phase':'source116_prepared_native_writes',**native_run}),flush=True)
        release.publish()
        target = inventory(copy)
        assert len(target['ordinary_tables']) == 51
        assert target['foreign_keys'] == before['foreign_keys']
        store,email = AutoReplyStore(copy),EmailStore(copy)
        store.upsert_codex_session_search_index(session_id='runs11-coupled-current',source_type='copy-only',source_id='current',title='runs11currentuniquetitle')
        setup_id = store.record_setup_wizard_event(step_id='runs11-coupled-current',action_id='copy',status='completed',summary='current')
        with store._connect() as db:
            sample_id = insert_run11_fact(db,'email_classifier_runtime_samples',dict(model_id='runs11-coupled-current',outcome='success',cache_hit=0,runtime_warm=1,queue_ms=1,http_ms=2,embedding_ms=3,head_ms=4,total_ms=10,recorded_at='copy-only-time')).lastrowid
            old_id = insert_run(db,'agent_runtime_attempts',dict(workload_kind='other',workload_key='runs11-coupled-current',attempt_number=1,route_name='copy',runtime_kind='copy',credential_mode='copy',model='copy',status='completed')).lastrowid
            streams = dict(db.execute('select name,seq from main.sqlite_sequence'))
        assert facts(copy,current=True,original_bounds=bounds) == original_facts
        expected = facts(copy,current=True)
        job=store.begin_wechat_memory_import_job(import_run_id='tasks20-copy-current',account_id='copy-only')
        assert job == 2
        store.finish_wechat_memory_import_job(job,status='completed',error='copy-only-current')
        current_wechat = wechat_facts(copy, current=True)
        store.set_service_state('state_copy_current','current')
        email.record_scan_cursor(account_id='copy-only-state-account',folder='copy-only-state-folder',uidvalidity=1,last_seen_uid=7)
        print(json.dumps({'phase':'current51_public_writes','setup_public_id':setup_id,'sample_public_id':sample_id,'old8_physical_id':old_id}),flush=True)
        release.rollback()
        restored=inventory(copy)
        assert wechat_facts(copy, current=False) == current_wechat
        assert restored['ordinary_tables'] == before['ordinary_tables']
        assert restored['declarations'] == before['declarations']
        assert restored['foreign_keys'] == before['foreign_keys']
        assert facts(copy,current=False) == expected
        assert facts(copy,current=False,original_bounds=bounds) == original_facts
        changes={name for name,value in before['facts'].items() if restored['facts'][name] != value}
        assert changes == {*NAMES,'service_state','email_scan_cursors','wechat_memory_import_jobs'},changes
        assert restored['original_witness_facts'] == before['original_witness_facts']
        expected_sequence=dict(before['sequence'])
        expected_sequence['wechat_memory_import_jobs']=2
        expected_sequence['runs']=streams['runs:published']
        for name in NAMES[1:]:
            expected_sequence[name]=streams['runs:'+name]
        assert dict(restored['sequence']) == expected_sequence
        with closing(sqlite3.connect(copy)) as db:
            assert db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == release.original_version
            assert tuple(db.execute('select version,applied_at from email_schema_migrations order by version')) == release.versions
            assert db.execute("select rowid from codex_session_search_fts where codex_session_search_fts match 'runs11currentuniquetitle'").fetchall()
        cold=json.loads(run([sys.executable,'-B','-c',old_cold,str(copy)],root=old_root,env=old_env))
        old_writes=json.loads(run([sys.executable,'-B','-c',PREDECESSOR_WRITE,str(copy)],root=old_root,env=old_env))
        predecessor_facts=facts(copy,current=False)
        predecessor_wechat = wechat_facts(copy, current=False)
        assert facts(copy,current=False,original_bounds=bounds) == original_facts
        print(json.dumps({'phase':'inverse116_genuine_predecessor_writes',**old_writes,**cold}),flush=True)
        repeated=prepare_database_schema_release(root,copy)
        repeated.publish()
        repeated.verify_loaded()
        final_store, final_email = AutoReplyStore(copy), EmailStore(copy)
        assert final_store.get_service_state('state_copy_current') == 'current'
        assert final_store.get_service_state('state_copy_predecessor') == 'predecessor'
        assert final_email.get_scan_cursor('copy-only-state-account', 'copy-only-state-folder')['last_seen_uid'] == 11
        assert wechat_facts(copy, current=True) == predecessor_wechat
        assert facts(copy,current=True) == predecessor_facts
        assert facts(copy,current=True,original_bounds=bounds) == original_facts
        final=inventory(copy)
        assert final['ordinary_tables'] == target['ordinary_tables']
        assert final['foreign_keys'] == before['foreign_keys']
        assert run(['git','rev-parse','HEAD'],root=production) == predecessor
        result={'status':'passed','at_utc':datetime.now(timezone.utc).isoformat(),
            'ordinary_table_sequence':[116,51,116,51],'source_sha256':SOURCE_SHA,
            'genuine_predecessor_commit':predecessor,'current_and_predecessor_runs11_facts_types_ids_exact':True,
            'all_original_runs11_facts_preserved':True,'old8_write_after_preparation':native_run,
            'current_writes':{'setup_public_id':setup_id,'sample_public_id':sample_id,'old8_physical_id':old_id},
            'genuine_predecessor_writes':old_writes,'unaffected_source_families_exact':116-len(changes),
            'all_source_declarations_restored':True,'original_markers_and_full_email_ledger_restored':True,
            'inverse_current_native_counters_exact':True,'logical_title_fts_rebuilt':True,
            'source_foreign_key_findings_preserved':before['foreign_keys'],
            'source_unchanged':file_hash(source)==SOURCE_SHA,
            'checker_sha256':checker_sha,
            'current_and_predecessor_state_cursor_and_full_wechat_facts_survive_inverse_reforward':True,
            'candidate_app_hashes':{str(p.relative_to(root)):file_hash(p) for p in sorted((root/'app').glob('*.py'))},
            'elapsed_seconds':round(time.monotonic()-start,3),
            'boundary':'Actual source copy and real paired JSON child/current and genuine predecessor constructors. Sample fact uses native writer without retention; public retention covered separately. No Agent CLI/provider/outbound/UI/production writes or final11 acceptance.'}
    result['owned_temporary_directory_removed']=not owned.exists()
    assert result['owned_temporary_directory_removed'] and result['source_unchanged']
    assert file_hash(Path(__file__)) == checker_sha, 'checker changed during execution'
    receipt_path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-root',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--production-root',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    inside(args.candidate_root.resolve(),args.source.resolve(),args.production_root.resolve(),args.receipt.resolve())
