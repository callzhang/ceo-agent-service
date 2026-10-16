"""Disposable frozen-source Tasks20 component rehearsal; no runtime selection.

Not a cold Store constructor, coupled release, native CLI or production proof.
Only aggregate metadata leaves the owned temporary directory.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
SOURCE=Path('/private/tmp/ceo-business-combined-actual-u6i3ldg0/source.sqlite3')
HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('task20_release_candidate',HERE/'2026-10-16-task20-release-candidate.py')
release=importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()


def ordinary(db,schema='main'):
    return sorted(row[1] for row in db.execute('pragma '+schema+'.table_list')
                  if row[0]==schema and row[2]=='table' and not row[1].startswith('sqlite_'))


def baseline(db):
    return sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))


def main():
    from app.task8_storage import register_task8_functions
    from app.task12_storage import update_task12
    started=time.monotonic()
    source_hash=digest(SOURCE)
    assert source_hash=='970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320'
    owned=Path(tempfile.mkdtemp(prefix='task20-release-copy-',dir='/tmp')).resolve()
    report={'started_utc':datetime.now(timezone.utc).isoformat(),'scope':'unselected Tasks20 component on frozen nonempty source; no constructors/coupled markers/provider/native CLI/production',
            'source_sha256':source_hash,'candidate_sha256':digest(HERE/'2026-10-16-task20-release-candidate.py')}
    try:
        path=owned/'candidate.sqlite3'
        with closing(sqlite3.connect(SOURCE.as_uri()+'?mode=ro',uri=True)) as original, closing(sqlite3.connect(path,uri=True)) as db:
            original.backup(db)
            register_task8_functions(db)
            names=ordinary(db)
            foreign_keys=baseline(db)
            original_schema=[tuple(row) for row in db.execute("select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' order by type,name")]
            original_sequence=sorted(db.execute('select name,seq from sqlite_sequence'))
            report['original_ordinary_tables']=len(names)
            report['source_foreign_key_findings']=foreign_keys
            db.execute('begin immediate')
            metadata=release.capture_task20_source(db)
            report['source_variant']=metadata['variant']
            report['forward_source_counts']=release.migrate_task20_schema(db,metadata)
            assert baseline(db)==foreign_keys
            assert len(ordinary(db))==len(names)-12
            report['target_ordinary_tables']=len(ordinary(db))
            db.commit()
        # A new sqlite connection, deliberately not a current Store initializer.
        with closing(sqlite3.connect(path,uri=True)) as db:
            register_task8_functions(db)
            release._target_shape(db,metadata)
            db.execute('begin immediate')
            report['inverse_counts']=release.restore_task20_schema(db,metadata)
            db.commit()
            restored_schema=[tuple(row) for row in db.execute("select type,name,tbl_name,sql from sqlite_schema where name not like 'sqlite_%' order by type,name")]
            assert restored_schema==original_schema
            assert sorted(db.execute('select name,seq from sqlite_sequence'))==original_sequence
            assert ordinary(db)==names
            assert baseline(db)==foreign_keys
            assert db.execute('pragma quick_check').fetchone()[0]=='ok'
            db.execute('attach database ? as original',(SOURCE.as_uri()+'?mode=ro',))
            # Every ordinary source family, values and exact storage classes.
            for name in names:
                fields=tuple(row[1] for row in db.execute('pragma main.table_xinfo('+release._quote(name)+')'))
                columns=','.join(map(release._quote,fields))
                release._same(db,'select '+columns+' from original.'+release._quote(name),
                              'select '+columns+' from main.'+release._quote(name),fields)
            db.execute('detach database original')
            report['inverse_all_original_tables_typed_equal']=len(names)
            # Current target write, inverse, native predecessor-source SQL write,
            # reforward. This does not claim genuine archived app initialization.
            db.execute('begin immediate')
            release.migrate_task20_schema(db,metadata)
            current=release.insert_task12(db,'wechat_memory_import_jobs',{'import_run_id':'task20-copy-current-write','account_id':'synthetic'})
            update_task12(db,'wechat_memory_import_jobs',current.lastrowid,{'status':'completed','error':'copy receipt retained'})
            release.restore_task20_schema(db,metadata)
            assert db.execute("select status,error from wechat_memory_import_jobs where id=?",(current.lastrowid,)).fetchone()==('completed','copy receipt retained')
            predecessor=db.execute("insert into wechat_memory_import_jobs(import_run_id,account_id) values('task20-copy-native-write','synthetic')").lastrowid
            second=release.capture_task20_source(db)
            release.migrate_task20_schema(db,second)
            assert db.execute("select wechat_memory_import_jobs__id from tasks where record_type='wechat_memory_import_jobs' and wechat_memory_import_jobs__import_run_id='task20-copy-native-write'").fetchone()==(predecessor,)
            assert baseline(db)==foreign_keys
            db.commit()
            report['current_fact_inverse_native_sql_reforward']=True
            report['quick_check']=db.execute('pragma quick_check').fetchone()[0]
            report['page_size']=db.execute('pragma page_size').fetchone()[0]
            report['page_count']=db.execute('pragma page_count').fetchone()[0]
            report['freelist_count']=db.execute('pragma freelist_count').fetchone()[0]
            report['copy_file_bytes']=path.stat().st_size
        assert digest(SOURCE)==source_hash
        report['source_unchanged']=True
        report['elapsed_seconds']=round(time.monotonic()-started,3)
    finally:
        shutil.rmtree(owned)
    report['owned_temp_removed']=not owned.exists()
    destination=Path(os.environ['TASK20_COPY_REPORT']) if 'TASK20_COPY_REPORT' in os.environ else None
    if destination:
        destination.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,separators=(',',':')))


if __name__=='__main__':
    main()
