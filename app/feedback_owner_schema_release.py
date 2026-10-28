"""Explicit Store28.2 -> Store28.3 Feedback fact-owner retirement/inverse."""
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3

from app.conversation_session_schema_release import session_target_objects
from app.state_storage import update_state_rows
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = '2026-10-28.2'
TARGET_STORE_VERSION = '2026-10-28.3'
ROUND_NEUTRALS = {
    'workbench_task_id': '', 'workbench_turn_id': '', 'attempt_id': 0,
    'agent_run_id': 0, 'commit_sha': '', 'test_evidence_json': '{}',
    'restart_evidence_json': '{}', 'health_evidence_json': '{}', 'note': '',
}
OWNER_JOINS = """
left join feedback_processing_rounds r on r.id=i.current_round_id
 and r.feedback_key=i.feedback_key and r.batch_id=i.batch_id
left join actions a on a.record_type='feedback_events' and a.fb_event_key=i.feedback_key"""
JOIN_OWNERS = 'from feedback_processing_items i'+OWNER_JOINS


@lru_cache(maxsize=1)
def feedback_target_objects():
    objects = {row[1]: row for row in session_target_objects()}
    tables = json.loads((Path(__file__).parent/'schema_fixtures'/'store28_3_feedback_item.json').read_text())
    if set(tables) != {'feedback_processing_items'}:
        raise ValueError('invalid Store28.3 frozen Feedback declaration')
    objects['feedback_processing_items'] = ('table', 'feedback_processing_items', 'feedback_processing_items', tables['feedback_processing_items'])
    result = tuple(sorted(objects.values(), key=lambda row: (row[0], row[1])))
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if digest != 'ec77e9b01dc0915a387ed8b01d8d28cd9f150d7addaed9262b3ecb9d2b129a0a':
        raise ValueError('invalid Store28.3 frozen target catalog')
    return result


def _objects(db):
    return tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema "
                            "where name not glob 'sqlite_*' order by type,name"))


def _marker(db):
    return db.execute("select value,updated_at from state where record_type='service_state' "
                      "and key='store_schema_version'").fetchone()


def _foreign_keys(db):
    return tuple(sorted(db.execute('pragma foreign_key_check')))


def _require_owners(db, *, mirrors):
    invalid = ("typeof(i.current_round_id)<>'integer' or i.current_round_id<0 "
               "or a.id is null or (i.current_round_id>0 and r.id is null)")
    if mirrors:
        invalid += ' or i.resolved_at is not a.fb_event_resolved_at or typeof(i.resolved_at)<>typeof(a.fb_event_resolved_at)'
        for field, neutral in ROUND_NEUTRALS.items():
            expected = f'case when i.current_round_id=0 then {repr(neutral)} else r."{field}" end'
            invalid += f' or i."{field}" is not ({expected}) or typeof(i."{field}")<>typeof({expected})'
    if db.execute('select 1 '+JOIN_OWNERS+' where '+invalid+' limit 1').fetchone():
        raise ValueError('Feedback current owner is missing, conflicting or non-neutral')


def _rebuild_items(db, *, inverse):
    if db.execute("select 1 from temp.sqlite_schema where name='_store28_feedback_item'").fetchone():
        raise ValueError('Store28.3 transfer TEMP collision')
    # Only the four retained columns enter TEMP. Duplicate facts are never archived.
    db.execute('create temp table _store28_feedback_item as select task_id,feedback_key,batch_id,current_round_id from main.feedback_processing_items')
    catalog = session_target_objects() if inverse else feedback_target_objects()
    definitions = tuple(row for row in catalog if row[2] == 'feedback_processing_items')
    db.execute('drop table main.feedback_processing_items')
    db.execute(next(row[3] for row in definitions if row[0] == 'table'))
    if inverse:
        fields = ','.join('"'+field+'"' for field in ROUND_NEUTRALS)
        projection = ','.join(f'case when i.current_round_id=0 then {repr(neutral)} else r."{field}" end' for field, neutral in ROUND_NEUTRALS.items())
        db.execute('insert into main.feedback_processing_items(task_id,feedback_key,batch_id,current_round_id,'+fields+',resolved_at) '
                   'select i.task_id,i.feedback_key,i.batch_id,i.current_round_id,'+projection+',a.fb_event_resolved_at '
                   'from temp._store28_feedback_item i'+OWNER_JOINS+' order by i.task_id')
    else:
        db.execute('insert into main.feedback_processing_items(task_id,feedback_key,batch_id,current_round_id) '
                   'select task_id,feedback_key,batch_id,current_round_id from temp._store28_feedback_item order by task_id')
    for kind, _, _, sql in definitions:
        if kind != 'table':
            db.execute(sql)
    db.execute('drop table temp._store28_feedback_item')


class FeedbackOwnerSchemaRelease:
    def __init__(self, path, *, source_marker, source_fk):
        self.path = Path(path).resolve()
        self.source_marker = tuple(source_marker)
        self.source_fk = tuple(source_fk)
        self.published = False

    def _verify(self, db, *, target):
        marker = _marker(db)
        if (_objects(db) != (feedback_target_objects() if target else session_target_objects())
                or marker is None or marker[0] != (TARGET_STORE_VERSION if target else SOURCE_STORE_VERSION)
                or (not target and tuple(marker) != self.source_marker)
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != 54
                or _foreign_keys(db) != self.source_fk):
            raise ValueError('Store28.3 release schema, marker or foreign-key mismatch')
        _require_owners(db, mirrors=not target)
        if db.execute('pragma quick_check').fetchone()[0] != 'ok':
            raise ValueError('Store28.3 integrity mismatch')

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            register_task8_functions(db)
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            try:
                self._verify(db, target=inverse)
                waters = tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))
                _rebuild_items(db, inverse=inverse)
                if tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid')) != waters:
                    raise ValueError('Store28.3 changed current allocation waters')
                marker = self.source_marker if inverse else (TARGET_STORE_VERSION,datetime.now(timezone.utc).isoformat())
                update_state_rows(db,'service_state',{'value':marker[0],'updated_at':marker[1]},
                                  where_sql='key=?',parameters=('store_schema_version',))
                self._verify(db, target=not inverse)
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._convert(inverse=False)

    def _verify_open(self, *, target):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            register_task8_functions(db)
            db.execute('begin')
            self._verify(db,target=target)

    def verify_loaded(self):
        self._verify_open(target=True)

    def rollback(self):
        if self.published:
            self._convert(inverse=True)
        else:
            self._verify_open(target=False)

    def finalize(self):
        self.verify_loaded()


def prepare_feedback_owner_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        register_task8_functions(db)
        db.execute('begin')
        marker = _marker(db)
        if (_objects(db) != session_target_objects() or marker is None
                or marker[0] != SOURCE_STORE_VERSION
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != 54):
            raise ValueError('unsupported Store28.2 / Email54 Feedback source')
        _require_owners(db,mirrors=True)
        return FeedbackOwnerSchemaRelease(path,source_marker=marker,source_fk=_foreign_keys(db))
