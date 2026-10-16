from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import pytest

from app.email_classification_schema import create_current_email_classifications
from app.action_storage import create_action_schema
from app.action_skill_config_storage import (
    insert_skill_receipt, load_receipt_projection, export_receipt_projection,
    list_skill_load_receipts, latest_skill_load_receipt_for_pid,
)
from app.configs_storage import create_configs_schema, delete_config, insert_config, update_config
from app.configs_definitions import CONFIG_FIELDS, config_projection
from app.schedules_storage import create_schedules_schema, insert_schedule, delete_schedule
from app.schedules_definitions import schedule_projection, SCHEDULE_FIELDS
from tests.configs_source_fixtures import ROOT, source_db, insert as source_insert


_COLD_SCHEMA_PROBE = """
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from app.email_store import EmailStore
from app.store import AutoReplyStore

path = Path(sys.argv[1])
if sys.argv[2] == 'repair':
    with sqlite3.connect(path) as db:
        db.execute('drop index idx_configs_revision_number')
        db.execute('drop index idx_schedules_enabled')
store = AutoReplyStore(path)
if sys.argv[2] != 'store':
    EmailStore(path)
assert store._schema_is_current()
with sqlite3.connect(path) as db:
    objects = db.execute(
        'select type,name,tbl_name,sql from sqlite_master '
        'where sql is not null order by type,name'
    ).fetchall()
    names = {row[1] for row in objects}
    assert {'idx_configs_revision_number', 'idx_schedules_enabled'} <= names
print(hashlib.sha256(json.dumps(objects, separators=(',', ':')).encode()).hexdigest())
"""


def test_current_schema_is_stable_across_cold_process_hash_seeds(tmp_path):
    def probe(path, seed, mode='open'):
        result = subprocess.run(
            [sys.executable, '-c', _COLD_SCHEMA_PROBE, str(path), mode],
            cwd=ROOT, env={**os.environ, 'PYTHONHASHSEED': str(seed)},
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    hashes = {
        seed: probe(tmp_path / f'fresh-{seed}.sqlite3', seed)
        for seed in (0, 1, 42)
    }
    assert len(set(hashes.values())) == 1
    path = tmp_path / 'fresh-0.sqlite3'
    assert probe(path, 1) == hashes[0]
    repair_path = tmp_path / 'repair.sqlite3'
    probe(repair_path, 0, 'store')
    assert probe(repair_path, 42, 'repair') == hashes[0]

@pytest.fixture
def target():
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('pragma foreign_keys=on')
        create_schedules_schema(db)
        create_current_email_classifications(db)
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text)")
        business = json.loads((ROOT / 'docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json').read_text())['actual']['tables']
        for name in ('business_tasks','business_task_signals'):
            db.execute(business[name]['sql'])
        create_action_schema(db, include_reply=True, reply_variant='fresh_memory_fk', include_email=True,
                             include_followup_sync=True, include_skill_config_receipts=True)
        create_configs_schema(db)
        db.commit()
        db.execute('begin')
        yield db

def skill(db, id=1):
    insert_config(db,'managed_skills', {'id':id,'name':f's-{id}','display_name':'Skill','created_at':'t'})

def revision(db,id=10,skill_id=1,digest='sha'):
    insert_config(db,'managed_skill_revisions', {'id':id,'skill_id':skill_id,'revision_number':1,
        'content':'body','sha256':digest,'source':'test','created_at':'t'})

def runtime(db,id=30):
    insert_config(db,'runtime_skill_configs',{'id':id,'status':'pending_restart','created_at':'t'})

@pytest.mark.parametrize('variant',('actual','fresh'))
@pytest.mark.parametrize('kind,parent_field,physical_ref',(
    ('managed_skill_revisions','parent_revision_id','skill_revision_parent_ref'),
    ('runtime_skill_configs','parent_id','runtime_config_parent_ref'),
))
def test_native_self_reference_insert_and_parent_affinity(target,variant,kind,parent_field,physical_ref):
    with source_db(variant) as source:
        if kind == 'managed_skill_revisions':
            parent_skill={'id':1,'name':'s-1','display_name':'Skill','created_at':'t'}
            source_insert(source,'managed_skills',parent_skill)
            insert_config(target,'managed_skills',parent_skill)

        # A deleted physical row leaves sqlite_sequence ahead of max(id).
        insert_config(target,'managed_skills',
                      {'id':99,'name':'gap','display_name':'Gap','created_at':'t'})
        assert delete_config(target,'managed_skills',99) == 1
        physical_seq=target.execute(
            "select seq from sqlite_sequence where name='configs'").fetchone()[0]
        physical_max=target.execute('select coalesce(max(id),0) from configs').fetchone()[0]
        assert physical_seq > physical_max

        def row(row_id,parent):
            if kind == 'managed_skill_revisions':
                return {'id':row_id,'skill_id':1,'revision_number':row_id,
                        'content':'body','sha256':f'sha-{row_id}','source':'test',
                        parent_field:parent,'created_at':'t'}
            return {'id':row_id,parent_field:parent,'status':'pending_restart','created_at':'t'}

        self_row=row(2,2)
        source_insert(source,kind,self_row)
        insert_config(target,kind,self_row)
        fields=','.join(CONFIG_FIELDS[kind])
        assert target.execute(config_projection(kind)).fetchone() == source.execute(
            f'select {fields} from {kind}').fetchone()
        physical=target.execute(
            f'select id,{physical_ref} from configs where record_type=?',(kind,)
        ).fetchone()
        assert physical[0] == physical[1]
        assert target.execute('pragma foreign_key_check').fetchall() == []

        for candidate in (row(3,999),row(4,b'4')):
            with pytest.raises(sqlite3.IntegrityError):
                source_insert(source,kind,candidate)
            with pytest.raises(sqlite3.IntegrityError):
                insert_config(target,kind,candidate)

        null_row=row(5,None)
        source_insert(source,kind,null_row)
        insert_config(target,kind,null_row)
        assert target.execute(config_projection(kind)).fetchall() == source.execute(
            f'select {fields} from {kind} order by id').fetchall()

@pytest.mark.parametrize('raw', ('01','1.0',1.0))
@pytest.mark.parametrize('variant', ('actual','fresh'))
def test_numeric_parent_affinity_matches_source(target,variant,raw):
    with source_db(variant) as source:
        source_insert(source,'managed_skills',{'id':1,'name':'s-1','display_name':'Skill','created_at':'t'})
        skill(target)
        values={'id':10,'skill_id':raw,'revision_number':1,'content':'body','sha256':'sha','source':'test','created_at':'t'}
        source_insert(source,'managed_skill_revisions',values)
        insert_config(target,'managed_skill_revisions',values)
        fields='id,skill_id,revision_number,content,sha256,parent_revision_id,source,created_at'
        assert target.execute(config_projection('managed_skill_revisions')).fetchone() == source.execute('select '+fields+' from managed_skill_revisions').fetchone()
        assert target.execute("select skill_revision_skill_ref from configs where record_type='managed_skill_revisions'").fetchone()[0] > 0

@pytest.mark.parametrize('variant', ('actual','fresh'))
def test_blob_fk_not_coerced_to_text(target,variant):
    with source_db(variant) as source:
        skill(target)
        source_insert(source,'managed_skills',{'id':1,'name':'s-1','display_name':'Skill','created_at':'t'})
        row={'id':10,'skill_id':b'1','revision_number':1,'content':'body','sha256':'sha','source':'test','created_at':'t'}
        with pytest.raises(sqlite3.IntegrityError):
            source_insert(source,'managed_skill_revisions',row)
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(target,'managed_skill_revisions',row)

@pytest.mark.parametrize('kind,values',(
    ('managed_skills', {'id':1,'name':'s-1','display_name':'Skill','created_at':'t'}),
    ('scheduled_task_config_versions', {'id':1,'snapshot_json':'arbitrary'}),
))
def test_mutable_alias_rekey_and_linked_no_action(target,kind,values):
    insert_config(target,kind,values)
    assert update_config(target,kind,1,{},new_source_rowid=2)==1
    assert target.execute(config_projection(kind)).fetchone()[0]==2
    assert target.execute('select legacy_key,source_key,public_key,numeric_public_key from configs where record_type=?',(kind,)).fetchone()==('2','2','2',2)
    if kind=='managed_skills':
        revision(target,id=10,skill_id=2)
        with pytest.raises(sqlite3.IntegrityError):
            update_config(target,kind,2,{},new_source_rowid=3)
    else:
        insert_config(target,kind,{'id':3,'snapshot_json':'other'})
        with pytest.raises(sqlite3.IntegrityError):
            update_config(target,kind,2,{},new_source_rowid=3)

@pytest.mark.parametrize('variant',('actual','fresh'))
def test_schedule_delete_all_fields_and_stale_cas(target,variant):
    row={'id':500,'name':'cron','prompt':'p','cron_expression':'* * * * *','timezone':'UTC','runtime_id':'claude','enabled':1}
    with source_db(variant) as source:
        cols=tuple(row)
        source.execute('insert into scheduled_tasks('+','.join(cols)+') values('+','.join('?' for _ in cols)+')',tuple(row.values()))
        insert_schedule(target,row)
        # Source Store CAS mutates updated_at and deleted_at to same time.
        source.execute("update scheduled_tasks set updated_at=?,deleted_at=?,enabled=0,version=version+1 where id=? and version=? and deleted_at is null",('gone','gone',500,1))
        assert delete_schedule(target,500,1,'gone')==1
        assert delete_schedule(target,500,1,'later')==0
        fields=','.join(SCHEDULE_FIELDS)
        assert target.execute(schedule_projection()).fetchone()==source.execute('select '+fields+' from scheduled_tasks').fetchone()
        for field in SCHEDULE_FIELDS:
            assert target.execute('select typeof('+field+') from schedules').fetchone()==source.execute('select typeof('+field+') from scheduled_tasks').fetchone()
        assert target.execute('select updated_at,deleted_at,enabled,version from schedules').fetchone()==('gone','gone',0,2)

def test_receipt_bytes_activation_independent_sequence_and_sha_policy(target):
    for sid,digest in ((20,'digest-é'),(100,'digest-100')):
        skill(target,sid)
        revision(target,sid*10,sid,digest)
    runtime(target)
    for order,(sid,rid) in enumerate(((20,200),(100,1000))):
        insert_config(target,'runtime_skill_bindings',{'config_id':30,'skill_id':sid,'revision_id':rid,'enabled':1,'load_order':order,'purpose':''})
    expected=json.dumps({20:'digest-é',100:'digest-100'},sort_keys=True)
    assert expected == '{"20": "digest-\\u00e9", "100": "digest-100"}'
    with pytest.raises(ValueError):
        insert_skill_receipt(target,'runtime_skill_load_receipts',{'config_id':30,'pid':7,'loaded_json':'{}','created_at':'t'})
    assert target.execute("select count(*) from actions where record_type='runtime_skill_load_receipts'").fetchone()[0]==0
    assert insert_skill_receipt(target,'runtime_skill_load_receipts',{'config_id':'030','pid':7,'loaded_json':expected,'created_at':'t'},source_id=901)==901
    receipt_id=target.execute("select id from actions where record_type='runtime_skill_load_receipts'").fetchone()[0]
    receipt=load_receipt_projection(target,receipt_id)
    assert receipt.loaded_json.encode()==expected.encode() and receipt.config_id==30
    assert list_skill_load_receipts(target,30)==[receipt]
    assert latest_skill_load_receipt_for_pid(target,7)==receipt
    assert target.execute("select payload_json from actions where id=?",(receipt_id,)).fetchone()[0].find('loaded_json')==-1
    assert update_config(target,'runtime_skill_configs',30,{'status':'active'})==1
    assert insert_skill_receipt(target,'managed_skill_export_receipts',{'revision_id':'0200','sha256':'mismatch-allowed','path':'p','created_at':'t'},source_id=700)==700
    export_id=target.execute("select id from actions where record_type='managed_skill_export_receipts'").fetchone()[0]
    assert export_receipt_projection(target,export_id).sha256=='mismatch-allowed'
    assert insert_skill_receipt(target,'managed_skill_export_receipts',{'revision_id':200,'sha256':'anything','path':'p2','created_at':'t'})==701
    assert insert_skill_receipt(target,'runtime_skill_load_receipts',{'config_id':30,'pid':8,'error':'failed','loaded_json':'{}','created_at':'t'})==902
    assert target.execute('pragma foreign_key_check').fetchall()==[]

@pytest.mark.parametrize('variant',('actual','fresh'))
def test_three_auto_sequences_and_immutable_native_rows(target,variant):
    with source_db(variant) as source:
        for db,writer in ((source,source_insert),(target,insert_config)):
            writer(db,'managed_skills',{'id':700,'name':'s-700','display_name':'S','created_at':'t'})
            writer(db,'managed_skill_revisions',{'id':901,'skill_id':700,'revision_number':1,'content':'x','sha256':'d','source':'t','created_at':'t'})
            writer(db,'runtime_skill_configs',{'id':901,'status':'pending_restart','created_at':'t'})
        for kind,values in (
            ('managed_skills',{'name':'s-next','display_name':'S','created_at':'t'}),
            ('managed_skill_revisions',{'skill_id':700,'revision_number':2,'content':'x','sha256':'e','source':'t','created_at':'t'}),
            ('runtime_skill_configs',{'status':'pending_restart','created_at':'t'}),
        ):
            native=source_insert(source,kind,values).lastrowid
            stored=insert_config(target,kind,values).lastrowid
            assert native==stored==({'managed_skills':701,'managed_skill_revisions':902,'runtime_skill_configs':902}[kind])
        for kind in ('managed_skill_revisions','runtime_feedback_iteration_capabilities','runtime_skill_bindings'):
            if kind=='runtime_feedback_iteration_capabilities':
                values={'config_id':901,'enabled':1,'created_at':'t'}
            elif kind=='runtime_skill_bindings':
                values={'config_id':901,'skill_id':700,'revision_id':901,'enabled':1,'load_order':0,'purpose':''}
            else:
                continue
            source_insert(source,kind,values)
            insert_config(target,kind,values)
        for kind in ('managed_skill_revisions','runtime_feedback_iteration_capabilities','runtime_skill_bindings'):
            with pytest.raises(sqlite3.IntegrityError):
                source.execute(f'update {kind} set rowid=rowid where rowid=(select rowid from {kind} limit 1)')
            with pytest.raises(sqlite3.IntegrityError):
                target.execute('update configs set legacy_key=legacy_key where record_type=?',(kind,))
        assert target.execute('pragma foreign_key_check').fetchall()==[]

@pytest.mark.parametrize('variant',('actual','fresh'))
def test_rekey_source_target_native_alias(target,variant):
    with source_db(variant) as source:
        for kind,values in (
            ('managed_skills',{'id':1,'name':'s-1','display_name':'S','created_at':'t'}),
            ('scheduled_task_config_versions',{'id':1,'snapshot_json':'arbitrary'}),
        ):
            source_insert(source,kind,values)
            insert_config(target,kind,values)
            source.execute(f'update {kind} set id=? where id=?',(2,1))
            assert update_config(target,kind,1,{},new_source_rowid=2)==1
            projection=config_projection(kind)
            assert target.execute(projection).fetchone()==source.execute('select '+','.join(values)+' from '+kind).fetchone()
            assert target.execute('select legacy_key,source_key from configs where record_type=?',(kind,)).fetchone()==('2','2')

def test_real_schedule_parent_and_wrong_kind_fk(target):
    with pytest.raises(sqlite3.IntegrityError):
        insert_config(target,'scheduled_task_skill_refs',{'scheduled_task_id':98765,'skill_source':'managed','skill_name':'x','position':0})
    insert_schedule(target,{'id':500,'name':'cron','prompt':'p','cron_expression':'* * * * *','timezone':'UTC','runtime_id':'claude','enabled':1})
    with pytest.raises(sqlite3.IntegrityError):
        insert_config(target,'scheduled_task_skill_refs',{'scheduled_task_id':500,'skill_source':'managed','skill_name':'x','managed_skill_id':12345,'position':0})

def test_snapshot_raw_text_unique_and_no_json_check(target):
    first=insert_config(target,'scheduled_task_config_versions',{'snapshot_json':'not-json'})
    assert first.lastrowid==1
    with pytest.raises(sqlite3.IntegrityError):
        insert_config(target,'scheduled_task_config_versions',{'snapshot_json':'not-json'})

@pytest.mark.parametrize('variant',('actual','fresh'))
def test_receipt_public_reader_casts_blob_text_like_store(target,variant):
    from app.action_skill_config_storage import latest_managed_skill_export_receipt
    from app.store import AutoReplyStore
    probe=json.loads((ROOT / 'docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json').read_text())['actual_frozen_receipt_metadata']
    with source_db(variant) as source:
        source.row_factory=sqlite3.Row
        for spec in probe.values():
            for obj in sorted(spec['objects'],key=lambda item: {'table':0,'index':1,'trigger':2}[item['type']]):
                source.execute(obj['sql'])
        s={'id':1,'name':'s-1','display_name':'Skill','created_at':'t'}
        r={'id':2,'skill_id':1,'revision_number':1,'content':'body','sha256':'sha','source':'test','created_at':'t'}
        c={'id':3,'status':'pending_restart','created_at':'t'}
        for kind,row in (('managed_skills',s),('managed_skill_revisions',r),('runtime_skill_configs',c)):
            source_insert(source,kind,row)
            insert_config(target,kind,row)
        source.execute('insert into managed_skill_export_receipts(id,revision_id,sha256,path,created_at) values(?,?,?,?,?)',
                       (20,2,b'sha',b'/tmp/p',b'time'))
        insert_skill_receipt(target,'managed_skill_export_receipts',
                             {'revision_id':2,'sha256':b'sha','path':b'/tmp/p','created_at':b'time'},source_id=20)
        source.execute('insert into runtime_skill_load_receipts(id,config_id,pid,loaded_json,error,created_at) values(?,?,?,?,?,?)',
                       (30,3,7,'{}',b'error',b'time'))
        insert_skill_receipt(target,'runtime_skill_load_receipts',
                             {'config_id':3,'pid':7,'loaded_json':'{}','error':b'error','created_at':b'time'},source_id=30)
        native_export=source.execute('select id,revision_id,sha256,path,created_at from managed_skill_export_receipts').fetchone()
        native_load=source.execute('select id,config_id,pid,loaded_json,error,created_at from runtime_skill_load_receipts').fetchone()
        expected_export=tuple(AutoReplyStore._managed_skill_export_receipt_from_row(native_export).__dict__.values())
        expected_load=tuple(AutoReplyStore._runtime_skill_load_receipt_from_row(native_load).__dict__.values())
        export_id=target.execute("select id from actions where record_type='managed_skill_export_receipts'").fetchone()[0]
        load_id=target.execute("select id from actions where record_type='runtime_skill_load_receipts'").fetchone()[0]
        assert tuple(export_receipt_projection(target,export_id).__dict__.values())==expected_export
        assert tuple(load_receipt_projection(target,load_id).__dict__.values())==expected_load
        assert latest_managed_skill_export_receipt(target,2)==export_receipt_projection(target,export_id)
        assert latest_skill_load_receipt_for_pid(target,7)==load_receipt_projection(target,load_id)
        # Storage classes remain native, while the public DTO casts TEXT-like BLOBs.
        assert tuple(source.execute('select typeof(error),typeof(created_at) from runtime_skill_load_receipts').fetchone())==('blob','blob')
        assert target.execute('select typeof(skill_error),typeof(skill_created_at) from actions where id=?',(load_id,)).fetchone()==('blob','blob')

@pytest.mark.parametrize('variant',('actual','fresh'))
@pytest.mark.parametrize('kind',('managed_skills','managed_skill_revisions','runtime_skill_configs',
                                 'managed_skill_export_receipts','runtime_skill_load_receipts'))
def test_auto_max_full_rolls_back_caller_and_savepoint(target,variant,kind):
    probe=json.loads((ROOT / 'docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json').read_text())['actual_frozen_receipt_metadata']
    maximum=2**63-1
    with source_db(variant) as source:
        for spec in probe.values():
            for obj in sorted(spec['objects'],key=lambda item: {'table':0,'index':1,'trigger':2}[item['type']]):
                source.execute(obj['sql'])
        parent_skill={'id':1,'name':'parent','display_name':'Parent','created_at':'t'}
        parent_revision={'id':2,'skill_id':1,'revision_number':1,'content':'x','sha256':'s','source':'t','created_at':'t'}
        parent_config={'id':3,'status':'pending_restart','created_at':'t'}
        for parent,row in (('managed_skills',parent_skill),('managed_skill_revisions',parent_revision),('runtime_skill_configs',parent_config)):
            source_insert(source,parent,row)
            insert_config(target,parent,row)
        rows={
            'managed_skills': {'id':maximum,'name':'max','display_name':'Max','created_at':'t'},
            'managed_skill_revisions': {'id':maximum,'skill_id':1,'revision_number':2,'content':'y','sha256':'t','source':'t','created_at':'t'},
            'runtime_skill_configs': {'id':maximum,'status':'pending_restart','created_at':'t'},
            'managed_skill_export_receipts': {'id':maximum,'revision_id':2,'sha256':'s','path':'p','created_at':'t'},
            'runtime_skill_load_receipts': {'id':maximum,'config_id':3,'pid':7,'loaded_json':'{}','error':'','created_at':'t'},
        }
        seed=rows[kind]
        if kind in probe:
            source.execute('insert into '+kind+'('+','.join(seed)+') values('+','.join('?' for _ in seed)+')',tuple(seed.values()))
            insert_skill_receipt(target,kind,{k:v for k,v in seed.items() if k!='id'},source_id=maximum)
        else:
            source_insert(source,kind,seed)
            insert_config(target,kind,seed)
        for db in (source,target):
            db.execute('create table max_marker(note text)')
            db.commit()
        errors=[]
        for db,native in ((source,True),(target,False)):
            db.execute('begin')
            db.execute("insert into max_marker values('pending')")
            db.execute('savepoint max_case')
            try:
                if native:
                    values={k:v for k,v in seed.items() if k!='id'}
                    db.execute('insert into '+kind+'('+','.join(values)+') values('+','.join('?' for _ in values)+')',tuple(values.values()))
                elif kind in probe:
                    insert_skill_receipt(db,kind,{k:v for k,v in seed.items() if k!='id'})
                else:
                    insert_config(db,kind,{k:v for k,v in seed.items() if k!='id'})
            except sqlite3.OperationalError as exc:
                errors.append((exc.sqlite_errorcode,exc.sqlite_errorname,db.in_transaction,
                               db.execute('select count(*) from max_marker').fetchone()[0]))
            else:
                pytest.fail('MAXROWID AUTOINCREMENT unexpectedly inserted')
            with pytest.raises(sqlite3.OperationalError):
                db.execute('release max_case')
        assert errors==[(13,'SQLITE_FULL',False,0)]*2
