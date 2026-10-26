"""Source/current-target transfer controls; no release or runtime selection."""
from contextlib import contextmanager
import json
import sqlite3
from contextlib import closing

import pytest

from app.configs_schema_transfer import (
    capture_config_declarations, copy_config_rows,
)
from app.action_storage import create_action_schema, action_schema_is_current
from app.configs_storage import create_configs_schema
from app.schedules_storage import create_schedules_schema
from app.email_classification_schema import create_current_email_classifications
from app.action_email_storage import _target_external_sql
from tests.configs_source_fixtures import ROOT, source_db, insert
from tests.test_configs_storage import VALID, INSERT_ORDER
from tests.test_configs_skill_storage import (
    SYNTHETIC_CONFIG_ROWS, INSERT_ORDER as SKILL_ORDER, SCHEDULE_ROW,
)
from tests.test_configs_target_integrated import target as target


@contextmanager
def native(variant):
    base = {'actual': 'actual', 'fresh': 'fresh',
            'actual_email52': 'actual', 'fresh_email52': 'fresh'}[variant]
    with source_db(base) as db:
        receipt = json.loads((ROOT / 'docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json').read_text())
        for spec in receipt['actual_frozen_receipt_metadata'].values():
            for obj in spec['objects']:
                if obj['type'] == 'table':
                    db.execute(obj['sql'])
            for obj in spec['objects']:
                if obj['type'] != 'table' and obj['sql']:
                    db.execute(obj['sql'])
        if variant in ('actual_email52', 'fresh_email52'):
            install_typed_account_blockers(db)
        yield db


def install_typed_account_blockers(db):
    names = {row[0] for row in db.execute("select name from sqlite_schema where type='trigger' and tbl_name='email_accounts'")}
    for name, sql in _target_external_sql().items():
        if name in names:
            db.execute('drop trigger "'+name+'"')
            db.execute(sql)


def seed(db):
    for kind in INSERT_ORDER:
        values = VALID[kind]
        if kind == 'email_accounts':
            values = {'rowid': -7, **values}
        insert(db, kind, values)
    columns = ','.join(SCHEDULE_ROW)
    db.execute(f"insert into scheduled_tasks({columns}) values({','.join('?' for _ in SCHEDULE_ROW)})",
               tuple(SCHEDULE_ROW.values()))
    for kind in SKILL_ORDER:
        values = SYNTHETIC_CONFIG_ROWS[kind]
        if kind == 'managed_skill_revisions':
            values = {**values, 'parent_revision_id': 200}
        if kind == 'runtime_skill_configs':
            values = {**values, 'parent_id': 300}
        insert(db, kind, values)
    db.execute("insert into managed_skill_export_receipts(id,revision_id,sha256,path,created_at) values(7001,200,?,?,?)",
               (b'raw-sha', b'raw-path', b'raw-time'))
    body = json.dumps({20: 'digest-é'}, sort_keys=True)
    db.execute("insert into runtime_skill_load_receipts(id,config_id,pid,loaded_json,error,created_at) values(9001,300,7,?,'','t')", (body,))
    db.execute("insert into runtime_skill_load_receipts(id,config_id,pid,loaded_json,error,created_at) values(9002,300,8,'{}','failed','t')")
    for kind in ('managed_skills', 'managed_skill_revisions', 'runtime_skill_configs',
                 'scheduled_tasks', 'managed_skill_export_receipts', 'runtime_skill_load_receipts'):
        db.execute('update sqlite_sequence set seq=seq+10000 where name=?', (kind,))
    db.commit()
    db.execute('begin')


def start_target(db):
    db.rollback()
    db.execute('pragma foreign_keys=off')
    db.execute('begin')


def facts(db, metadata):
    result = {}
    for kind, definition in metadata['tables'].items():
        fields = [c['name'] for c in definition['columns']]
        if kind in metadata['rowid_kinds']:
            fields.insert(0, 'rowid')
        selected = ','.join('"'+field+'"' for field in fields)
        result[kind] = sorted(db.execute(f'select {selected} from "{kind}"').fetchall(), key=repr)
    return result


def test_changed_source_schema_and_missing_snapshot_are_refused(target):
    start_target(target)
    with native('fresh') as source:
        with pytest.raises(Exception, match='transaction'):
            capture_config_declarations(source)
        source.execute('begin')
        source.execute('alter table managed_skills add column unrecognized text')
        with pytest.raises(ValueError, match='declaration'):
            capture_config_declarations(source)


@pytest.mark.parametrize('variant', ('actual_no_memory_fk', 'fresh_memory_fk'))
def test_skill_receipt_transfer_needs_email_scaffold_without_followup_types(variant):
    with closing(sqlite3.connect(':memory:')) as target, native('actual') as source:
        target.execute('create table tasks(id integer primary key,record_type text)')
        target.execute('create table runs(id integer primary key,record_type text)')
        create_current_email_classifications(target)
        create_schedules_schema(target)
        create_configs_schema(target)
        create_action_schema(target, include_reply=True, reply_variant=variant,
                             include_email=True, include_skill_config_receipts=True)
        assert action_schema_is_current(target, include_reply=True, reply_variant=variant,
                                        include_email=True, include_skill_config_receipts=True)
        target.commit()
        target.execute('begin')
        source.execute('begin')
        insert(source, 'runtime_skill_configs', {'id': 1, 'status': 'pending_restart'})
        source.execute("insert into runtime_skill_load_receipts(config_id,pid,loaded_json) values(1,7,'{}')")
        counts = copy_config_rows(source, target, capture_config_declarations(source))
        assert counts['runtime_skill_load_receipts'] == 1
        assert target.execute("select record_type from actions").fetchall() == [('runtime_skill_load_receipts',)]
        assert 'follow_up_drafts' not in target.execute("select sql from sqlite_schema where name='actions'").fetchone()[0]


def test_partial_account_blocker_translation_is_not_a_supported_source():
    with native('actual') as source:
        name = 'trg_email_direct_action_blocks_account_delete'
        source.execute('drop trigger '+name)
        source.execute(_target_external_sql()[name])
        source.execute('begin')
        with pytest.raises(ValueError, match='declaration'):
            capture_config_declarations(source)
