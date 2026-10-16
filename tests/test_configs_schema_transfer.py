"""Source/current-target transfer controls; no release or runtime selection."""
from contextlib import contextmanager
import json
import sqlite3

import pytest

from app.configs_schema_transfer import (
    capture_config_declarations, copy_config_rows, restore_config_rows,
)
from app.configs_storage import insert_config, update_config
from app.action_skill_config_storage import insert_skill_receipt
from tests.configs_source_fixtures import ROOT, source_db, insert
from tests.test_configs_storage import VALID, INSERT_ORDER
from tests.test_configs_skill_storage import (
    SYNTHETIC_CONFIG_ROWS, INSERT_ORDER as SKILL_ORDER, SCHEDULE_ROW,
)
from tests.test_configs_target_integrated import target as target


@contextmanager
def native(variant):
    with source_db(variant) as db:
        receipt = json.loads((ROOT / 'docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json').read_text())
        for spec in receipt['actual_frozen_receipt_metadata'].values():
            for obj in spec['objects']:
                if obj['type'] == 'table':
                    db.execute(obj['sql'])
            for obj in spec['objects']:
                if obj['type'] != 'table' and obj['sql']:
                    db.execute(obj['sql'])
        yield db


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


@pytest.mark.parametrize('variant', ('actual', 'fresh'))
def test_eighteen_families_roundtrip_current_facts_and_waters(target, variant):
    start_target(target)
    with native(variant) as source, native(variant) as restored:
        seed(source)
        metadata = capture_config_declarations(source)
        assert len(metadata['tables']) == 18
        assert sum(len(d['columns']) for d in metadata['tables'].values()) == 146
        assert 'rows' not in json.dumps(metadata)
        baseline = facts(source, metadata)
        counts = copy_config_rows(source, target, metadata)
        assert sum(counts.values()) == sum(map(len, baseline.values()))
        assert target.in_transaction
        assert target.execute('pragma foreign_key_check').fetchall() == []
        restored.execute('pragma foreign_keys=off')
        restored.execute('begin')
        restore_config_rows(target, restored, metadata)
        assert facts(restored, metadata) == baseline
        for kind in metadata['auto_kinds']:
            assert source.execute('select seq from sqlite_sequence where name=?', (kind,)).fetchone() == restored.execute('select seq from sqlite_sequence where name=?', (kind,)).fetchone()
        restored.rollback()

        # Inverse must consume current facts, not the old source values.
        assert update_config(target, 'managed_skills', 20, {'display_name': 'Changed'}) == 1
        identity = insert_skill_receipt(target, 'runtime_skill_load_receipts',
                                       {'config_id': 300, 'pid': 99, 'loaded_json': '{}', 'error': 'new failure'})
        assert identity == 19003
        restored.execute('begin')
        restore_config_rows(target, restored, metadata)
        assert restored.execute('select display_name from managed_skills where id=20').fetchone() == ('Changed',)
        assert restored.execute('select pid,error from runtime_skill_load_receipts where id=?', (identity,)).fetchone() == (99, 'new failure')
        assert restored.execute('pragma foreign_key_check').fetchall() == []
        assert source.execute('select display_name from managed_skills where id=20').fetchone() == ('Skill 20',)


@pytest.mark.parametrize('body', (' { } ', 'malformed', b'{}'))
def test_unreconstructable_body_is_rejected_before_target_writes(target, body):
    start_target(target)
    with native('actual') as source:
        seed(source)
        source.execute("insert into runtime_skill_load_receipts(config_id,pid,loaded_json,error) values(300,15,?,'failed')", (body,))
        metadata = capture_config_declarations(source)
        before = target.total_changes
        with pytest.raises(ValueError, match='reconstruct'):
            copy_config_rows(source, target, metadata)
        assert target.total_changes == before
        assert target.execute('select count(*) from configs').fetchone() == (0,)


def test_changed_source_schema_and_missing_snapshot_are_refused(target):
    start_target(target)
    with native('fresh') as source:
        with pytest.raises(Exception, match='transaction'):
            capture_config_declarations(source)
        source.execute('begin')
        source.execute('alter table managed_skills add column unrecognized text')
        with pytest.raises(ValueError, match='declaration'):
            capture_config_declarations(source)


@pytest.mark.parametrize('variant', ('actual', 'fresh'))
def test_cyclic_config_parent_graph_is_copied_without_mutating_immutable_rows(target, variant):
    start_target(target)
    with native(variant) as source:
        source.execute('pragma foreign_keys=off')
        source.execute('begin')
        source.execute("insert into runtime_skill_configs(id,parent_id,status) values(2,3,'pending_restart')")
        source.execute("insert into runtime_skill_configs(id,parent_id,status) values(3,2,'pending_restart')")
        assert source.execute('pragma foreign_key_check').fetchall() == []
        copy_config_rows(source, target, capture_config_declarations(source))
        pairs = target.execute("select id,runtime_config_parent_ref from configs where record_type='runtime_skill_configs' order by numeric_public_key").fetchall()
        assert pairs == [(1, 2), (2, 1)]
        assert target.execute('pragma foreign_key_check').fetchall() == []


def test_mid_copy_failure_leaves_rollback_to_caller(target, monkeypatch):
    import app.configs_schema_transfer as transfer
    start_target(target)
    with native('actual') as source:
        seed(source)
        metadata = capture_config_declarations(source)
        baseline = facts(source, metadata)
        def fail_receipt(*args, **kwargs):
            raise sqlite3.IntegrityError('copy interruption')
        monkeypatch.setattr(transfer, 'insert_skill_receipt', fail_receipt)
        with pytest.raises(sqlite3.IntegrityError, match='interruption'):
            copy_config_rows(source, target, metadata)
        assert target.in_transaction
        assert target.execute('select count(*) from configs').fetchone()[0] > 0
        target.rollback()
        assert target.execute('select count(*) from configs').fetchone() == (0,)
        assert target.execute('select count(*) from schedules').fetchone() == (0,)
        assert facts(source, metadata) == baseline


def test_late_binding_cannot_silently_change_old_receipt(target):
    start_target(target)
    with native('actual') as source:
        seed(source)
        insert(source, 'managed_skills', {'id': 21, 'name': 'late', 'display_name': 'Late'})
        insert(source, 'managed_skill_revisions', {'id': 201, 'skill_id': 21, 'revision_number': 1,
                                                'content': 'body', 'sha256': 'late', 'source': 'test'})
        insert(source, 'runtime_skill_bindings', {'config_id': 300, 'skill_id': 21, 'revision_id': 201,
                                               'enabled': 1, 'load_order': 1, 'purpose': ''})
        before = target.total_changes
        with pytest.raises(ValueError, match='reconstruct'):
            copy_config_rows(source, target, capture_config_declarations(source))
        assert target.total_changes == before


def test_inverse_rejects_broken_current_parent_before_writing_native_rows(target):
    start_target(target)
    with native('actual') as source, native('actual') as restored:
        seed(source)
        metadata = capture_config_declarations(source)
        copy_config_rows(source, target, metadata)
        insert_config(target, 'runtime_skill_configs',
                      {'id': 900, 'parent_id': 899, 'status': 'pending_restart'})
        restored.execute('pragma foreign_keys=off')
        restored.execute('begin')
        before = restored.total_changes
        with pytest.raises(ValueError, match='foreign keys'):
            restore_config_rows(target, restored, metadata)
        assert restored.total_changes == before


@pytest.mark.parametrize('variant', ('actual', 'fresh'))
def test_forward_can_share_release_connection_without_changing_native_facts(target, variant):
    start_target(target)
    with native(variant) as template:
        template.execute('begin')
        metadata = capture_config_declarations(template)
    for definition in metadata['tables'].values():
        target.execute(definition['sql'])
    for definition in metadata['tables'].values():
        for index in reversed(definition['indexes']):
            if index['sql']:
                target.execute(index['sql'])
        for trigger in definition['triggers']:
            target.execute(trigger['sql'])
    seed(target)
    before = facts(target, metadata)
    counts = copy_config_rows(target, target, metadata)
    assert counts == {kind: len(rows) for kind, rows in before.items()}
    assert facts(target, metadata) == before
    assert target.in_transaction
