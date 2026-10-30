"""Exact Store29.2 to Store30.1 Email Action owner publication and inverse."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from app.current_schema_manifest import current_objects, require_current_pair, schema_objects
from app.state_definitions import state_projection
from app.state_storage import upsert_state
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = '2026-10-29.2'
TARGET_STORE_VERSION = '2026-10-30.1'
EMAIL_VERSION = 54
_FIELDS = ('classification_id', 'account_id', 'parameters_json')
_DIRECT = "('archive','flag_important','mark_read','move','trash','label')"
_CHANGED_TRIGGERS = ('trg_configs_direct_account_update', 'trg_configs_direct_account_delete',
                     'trg_emails_direct_action_blocks_plan_switch')


@lru_cache(maxsize=1)
def source_objects():
    path = Path(__file__).with_name('schema_fixtures') / 'store29_2_email54_catalog.json'
    return tuple(tuple(row) for row in json.loads(path.read_text()))


def _marker(db):
    return db.execute(f"select value,updated_at from ({state_projection('service_state')}) where key='store_schema_version'").fetchone()


def _write_marker(db, version, timestamp):
    upsert_state(db, 'service_state', {'key': 'store_schema_version', 'value': version},
                 {'value': 'json_quote(excluded.value)', 'updated_at': 'json_quote(?)'}, update_parameters=(timestamp,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute('pragma foreign_key_check')))


def _validate_facts(db, *, source):
    from app.action_storage import email_action_projection
    plan = email_action_projection('email_action_plans')
    for action_id, payload, operation, plan_id in db.execute(
        "select id,json(action_data),action_operation,action_action_plan_id from actions where record_type='action'"
    ):
        data = json.loads(payload, object_pairs_hook=lambda pairs: pairs)
        keys = [key for key, _ in data]
        if source and any(keys.count(field) != 1 for field in _FIELDS):
            raise ValueError(f'Action {action_id} has missing or duplicate source keys')
        if not source and any(field in keys for field in _FIELDS):
            raise ValueError(f'Action {action_id} contains retired target keys')
        direct = operation in ('archive', 'flag_important', 'mark_read', 'move', 'trash', 'label') and plan_id is not None
        if direct:
            rows = db.execute(f"select classification_id,account_id,coalesce(json_extract(action_parameters_json, '$.'||?), '{{}}'), actions_json, json_type(action_parameters_json), json_type(action_parameters_json, '$.'||?) from ({plan}) where action_plan_id=?", (operation, operation, plan_id)).fetchall()
            if len(rows) != 1:
                raise ValueError(f'Action {action_id} has no exact Plan')
            classification, account, parameters, actions_json, root_type, parameter_type = rows[0]
            if (operation not in json.loads(actions_json) or root_type != 'object'
                    or parameter_type not in (None, 'object') or not isinstance(parameters, str)
                    or not isinstance(json.loads(parameters), dict)):
                raise ValueError(f'Action {action_id} has invalid Plan operation or parameters')
            if source:
                values = dict(data)
                for field, expected in zip(_FIELDS, (classification, account, parameters), strict=True):
                    actual = values[field]
                    if type(actual) is not type(expected) or actual != expected:
                        raise ValueError(f'Action {action_id} differs from Plan in {field}')
        elif source and any(dict(data)[field] is not None for field in _FIELDS):
            raise ValueError(f'non-email Action {action_id} has retired field values')


def _rebuild(db, *, inverse):
    from app.action_storage import email_action_projection
    objects = source_objects() if inverse else current_objects(email_ledger=True)
    columns = tuple(row[1] for row in db.execute('pragma table_xinfo(actions)') if row[6] == 0)
    def quote(name):
        return '"' + name.replace('"', '""') + '"'
    quoted = ','.join(map(quote, columns))
    sequences = tuple(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))
    db.execute(f'create temp table _email_owner_actions as select {quoted} from main.actions')
    if inverse:
        db.execute('create unique index temp._email_owner_actions_id on _email_owner_actions(id)')
        plan = email_action_projection('email_action_plans')
        # Values are materialized from the exact immutable Plan in the pre-rebuild database.
        rows = db.execute(f"select a.id,p.classification_id,p.account_id,coalesce(json_extract(p.action_parameters_json,'$.'||a.action_operation),'{{}}') from actions a join ({plan}) p on p.action_plan_id=a.action_action_plan_id where a.record_type='action' and a.action_operation in {_DIRECT}").fetchall()
        db.execute("update temp._email_owner_actions set action_data=jsonb_set(action_data,'$.classification_id',null,'$.account_id',null,'$.parameters_json',null) where _kind=70")
        for action_id, classification, account, parameters in rows:
            db.execute("update temp._email_owner_actions set action_data=jsonb_set(action_data,'$.classification_id',json(?),'$.account_id',json(?),'$.parameters_json',json(?)) where id=?", (json.dumps(classification), json.dumps(account, ensure_ascii=False), json.dumps(parameters, ensure_ascii=False), action_id))
    else:
        db.execute("update temp._email_owner_actions set action_data=jsonb_remove(action_data,'$.classification_id','$.account_id','$.parameters_json') where _kind=70")
    for name in _CHANGED_TRIGGERS:
        db.execute('drop trigger ' + quote(name))
    db.execute('drop table main.actions')
    db.execute(next(sql for kind, name, _, sql in objects if kind == 'table' and name == 'actions'))
    if columns != tuple(row[1] for row in db.execute('pragma table_xinfo(actions)') if row[6] == 0):
        raise ValueError('Email owner transition has an unmapped stored Actions column')
    db.execute(f'insert into main.actions({quoted}) select {quoted} from temp._email_owner_actions')
    for kind, name, owner, sql in objects:
        if (owner == 'actions' and kind != 'table') or name in _CHANGED_TRIGGERS:
            db.execute(sql)
    db.execute('delete from sqlite_sequence')
    db.executemany('insert into sqlite_sequence(rowid,name,seq) values(?,?,?)', sequences)
    db.execute('drop table temp._email_owner_actions')


@dataclass
class EmailActionOwnerRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def _require(self, db, *, source):
        actual = _marker(db)
        if (schema_objects(db) != (source_objects() if source else current_objects(email_ledger=True))
                or (tuple(actual or ()) != self.source_marker if source else actual is None or actual[0] != TARGET_STORE_VERSION)
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != EMAIL_VERSION):
            raise ValueError('Email Action owner release requires exact source/target schema and marker')
        if _foreign_keys(db) != self.source_fk:
            raise ValueError('Email Action owner release FK findings changed')
        _validate_facts(db, source=source)

    def _change(self, *, inverse):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            register_task8_functions(db)
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            try:
                self._require(db, source=not inverse)
                _rebuild(db, inverse=inverse)
                _write_marker(db, *(self.source_marker if inverse else (TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())))
                self._require(db, source=inverse)
                if db.execute('pragma quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Email Action owner transition integrity differs')
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._change(inverse=False)

    def verify_loaded(self):
        require_current_pair(self.database_path)

    def finalize(self):
        self.verify_loaded()

    def rollback(self):
        if self.published:
            self._change(inverse=True)


def prepare_email_action_owner_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        register_task8_functions(db)
        db.execute('begin')
        marker = _marker(db)
        if (schema_objects(db) != source_objects() or marker is None or marker[0] != SOURCE_STORE_VERSION
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != EMAIL_VERSION):
            raise ValueError('Unsupported Store29.2 / Email54 owner source')
        if db.execute('pragma quick_check').fetchone()[0] != 'ok':
            raise ValueError('Email Action owner source integrity differs')
        _validate_facts(db, source=True)
        return EmailActionOwnerRelease(path, tuple(marker), _foreign_keys(db))
