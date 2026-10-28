"""Explicit Store28.1 -> Store28.2 session-owner migration and current inverse."""
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3

from app.model_retirement_schema_release import retirement_target_objects
from app.state_storage import update_state_rows
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = '2026-10-28.1'
TARGET_STORE_VERSION = '2026-10-28.2'


@lru_cache(maxsize=1)
def session_target_objects():
    objects = {row[1]: row for row in retirement_target_objects()}
    tables = json.loads((Path(__file__).parent/'schema_fixtures'/'store28_2_session_state.json').read_text())
    if set(tables) != {'state'}:
        raise ValueError('invalid Store28.2 frozen state declaration')
    objects['state'] = ('table', 'state', 'state', tables['state'])
    result = tuple(sorted(objects.values(), key=lambda row: (row[0], row[1])))
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if digest != '8fe0b0d99c39c9bcab5b6637ba188677038cd620a72c78e7daa720a8d0d36b2a':
        raise ValueError('invalid Store28.2 frozen target catalog')
    return result


def _objects(db):
    return tuple(db.execute("select type,name,tbl_name,sql from sqlite_schema "
                            "where name not glob 'sqlite_*' order by type,name"))


def _marker(db):
    return db.execute("select value,updated_at from state where record_type='service_state' "
                      "and key='store_schema_version'").fetchone()


def _foreign_keys(db):
    return tuple(sorted(db.execute('pragma foreign_key_check')))


def _require_matching_owner(db):
    conflict = db.execute("""
        select 1 from state c left join state s
          on s.record_type='conversation_runtime_sessions'
         and s.conversation_id=c.conversation_id and s.route_name='codex_oauth'
        where c.record_type='conversations' and (
          (c.codex_session_id is not null and c.codex_session_id<>''
            and c.codex_session_id is not s.session_id)
          or (c.codex_session_contract_hash<>''
            and c.codex_session_contract_hash is not s.contract_hash)) limit 1
    """).fetchone()
    if conflict:
        raise ValueError('conversation session owner is missing or conflicting')


def _rebuild_state(db, *, inverse):
    if db.execute("select 1 from temp.sqlite_schema where name='_store28_session_state'").fetchone():
        raise ValueError('Store28.2 transfer TEMP collision')
    db.execute('create temp table _store28_session_state as '
               'select id,record_type,legacy_key,payload_json from main.state')
    # Canonical encoded leaves are transferred without decoding/re-encoding BLOB
    # or REAL values. Only conversations payload changes; no source copy survives.
    if inverse:
        db.execute("""
            update temp._store28_session_state as c set payload_json=json_set(
              c.payload_json,
              '$.codex_session_id',json(coalesce((select s.payload_json->'$.session_id'
                from main.state s join main.state original on original.id=c.id
                where s.record_type='conversation_runtime_sessions'
                and s.conversation_id=original.conversation_id
                and s.route_name='codex_oauth'),'null')),
              '$.codex_session_contract_hash',json(coalesce((select s.payload_json->'$.contract_hash'
                from main.state s join main.state original on original.id=c.id
                where s.record_type='conversation_runtime_sessions'
                and s.conversation_id=original.conversation_id
                and s.route_name='codex_oauth'),'""')))
            where c.record_type='conversations'
        """)
    else:
        db.execute("update temp._store28_session_state set payload_json=json_remove(payload_json,"
                   "'$.codex_session_id','$.codex_session_contract_hash') where record_type='conversations'")
    catalog = retirement_target_objects() if inverse else session_target_objects()
    definitions = tuple(row for row in catalog if row[2] == 'state')
    db.execute('drop table main.state')
    db.execute(next(row[3] for row in definitions if row[0] == 'table'))
    db.execute('insert into main.state(id,record_type,legacy_key,payload_json) '
               'select id,record_type,legacy_key,payload_json from temp._store28_session_state order by id')
    for kind, _, _, sql in definitions:
        if kind != 'table':
            db.execute(sql)
    db.execute('drop table temp._store28_session_state')


class ConversationSessionSchemaRelease:
    def __init__(self, path, *, source_marker, source_fk):
        self.path = Path(path).resolve()
        self.source_marker = tuple(source_marker)
        self.source_fk = tuple(source_fk)
        self.published = False

    def _verify(self, db, *, target):
        expected = session_target_objects() if target else retirement_target_objects()
        marker = _marker(db)
        if (_objects(db) != expected or marker is None
                or marker[0] != (TARGET_STORE_VERSION if target else SOURCE_STORE_VERSION)
                or (not target and tuple(marker) != self.source_marker)
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != 54
                or _foreign_keys(db) != self.source_fk):
            raise ValueError('Store28.2 release schema, marker or foreign-key mismatch')
        if not target:
            _require_matching_owner(db)
        if db.execute('pragma quick_check').fetchone()[0] != 'ok':
            raise ValueError('Store28.2 release integrity mismatch')

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            register_task8_functions(db)
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            try:
                self._verify(db, target=inverse)
                waters = tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))
                _rebuild_state(db, inverse=inverse)
                if tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid')) != waters:
                    raise ValueError('Store28.2 changed current allocation waters')
                marker = self.source_marker if inverse else (
                    TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                update_state_rows(db, 'service_state', {'value': marker[0], 'updated_at': marker[1]},
                                  where_sql='key=?', parameters=('store_schema_version',))
                self._verify(db, target=not inverse)
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._convert(inverse=False)

    def _verify_open(self, *, target):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True)) as db:
            register_task8_functions(db)
            db.execute('begin')
            self._verify(db, target=target)

    def verify_loaded(self):
        self._verify_open(target=True)

    def rollback(self):
        if self.published:
            self._convert(inverse=True)
        else:
            self._verify_open(target=False)

    def finalize(self):
        self.verify_loaded()


def prepare_conversation_session_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        register_task8_functions(db)
        db.execute('begin')
        marker = _marker(db)
        if (_objects(db) != retirement_target_objects() or marker is None
                or marker[0] != SOURCE_STORE_VERSION
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != 54):
            raise ValueError('unsupported Store28.1 / Email54 session source')
        _require_matching_owner(db)
        return ConversationSessionSchemaRelease(path, source_marker=marker, source_fk=_foreign_keys(db))
