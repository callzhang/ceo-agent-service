"""Explicit Store19.11 -> 19.12 declaration convergence and current-data inverse."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import json

from app import action_storage as actions
from app.action_email_storage import _columns, _quote
from app.business_legacy_fields import retained_action_payloads
from app.current_schema_manifest import STORE_VERSION, EMAIL_VERSION, current_objects, schema_objects, require_current_pair
from app.result_schema_helpers import _markers, _foreign_keys
from app.state_storage import update_state_rows
from app.task_lifecycle_jsonb import _array, _framed

SOURCE_VERSION = "2026-10-19.11"
VERSION_KEY = "store_schema_version"
STAGE = "_current_actions_transfer"
WATER = "_current_actions_water"


def source_objects(variant):
    from app.current_action_schema import source_current_action_objects
    objects = [obj for obj in current_objects(email_ledger=True)
               if obj[2] not in {'actions', 'action_receipts', 'action_observers', 'action_entity_identity_map'}]
    objects.extend((obj['type'], obj['name'], 'actions', obj['sql'])
                   for obj in source_current_action_objects())
    # The current direct-email guards use the unified Action projection.  The
    # Store19.12 predecessor owns the email_actions-shaped guard text, which
    # must remain exact for both preflight and inverse.  Keep this frozen
    # predecessor declaration separate from the current target factory.
    predecessor_guards = {
        "trg_configs_direct_account_delete": (
            "CREATE TRIGGER trg_configs_direct_account_delete before delete on configs "
            "when old.record_type='email_accounts' and exists(select 1 from actions a where "
            "a.record_type='email_actions' and a.email_account_id=old.public_key and "
            "a.email_status='processing') begin select raise(abort,'email_direct_action_in_flight'); end"
        ),
        "trg_configs_direct_account_update": (
            "CREATE TRIGGER trg_configs_direct_account_update before update on configs "
            "when old.record_type='email_accounts' and exists(select 1 from actions a where "
            "a.record_type='email_actions' and a.email_account_id=old.public_key and "
            "a.email_status='processing') begin select raise(abort,'email_direct_action_in_flight'); end"
        ),
        "trg_emails_direct_action_blocks_plan_switch": (
            "CREATE TRIGGER trg_emails_direct_action_blocks_plan_switch before update of "
            "class_status,class_current_action_plan_id on emails when old.classification_id is not null "
            "and new.classification_id is not null and (old.class_status is not new.class_status or "
            "old.class_current_action_plan_id is not new.class_current_action_plan_id) and exists("
            "select 1 from actions a where a.record_type='email_actions' and "
            "a.email_classification_id=old.classification_id and a.email_status='processing') "
            "begin select raise(abort,'email_direct_action_in_flight'); end"
        ),
    }
    objects = [
        (kind, name, table, predecessor_guards[name] if name in predecessor_guards else sql)
        for kind, name, table, sql in objects
    ]
    return tuple(sorted(objects, key=lambda obj: (obj[0], obj[1])))


def _layouts(variant):
    return retained_action_payloads(actions.action_payloads(
        variant, physical_provenance=True, selection_references=True, execution_owner=True,
        shared_lifecycle=True, specialized_claims=True, postfix_owner=True)).layouts


def _rebuild(db, variant, *, inverse):
    from app.current_action_schema import current_action_payloads, current_action_objects
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('Actions conversion requires a caller-owned FK-off transaction')
    if db.execute("select 1 from temp.sqlite_schema where name in (?,?)", (STAGE, WATER)).fetchone():
        raise ValueError('Actions transfer TEMP collision')
    before_fk = _foreign_keys(db)
    old_layouts, new_layouts = _layouts(variant), current_action_payloads().layouts
    source, target = (new_layouts, old_layouts) if inverse else (old_layouts, new_layouts)
    defaults = {field: db.execute('select jsonb(?)', (json.dumps(value),)).fetchone()[0]
                for field, value in actions._ATTEMPT_DEFAULTS.items() if field in actions._ATTEMPT_HISTORICAL}
    if inverse and variant == 'fresh_memory_fk':
        # The source has no owner for nonneutral values in these four fields.
        for field in actions._ATTEMPT_HISTORICAL:
            if db.execute(f"select 1 from actions where record_type='reply_attempts' "
                          f"and attempt_{field} is not ? limit 1", (actions._ATTEMPT_DEFAULTS[field],)).fetchone():
                raise ValueError('fresh-source inverse cannot represent retained Attempt facts')
    def convert(kind, blob, public_parent):
        if kind not in actions.REPLY_ACTION_TYPES:
            return blob
        width, cells = _array(blob)
        if len(cells) != len(source[kind].fields):
            raise ValueError('Actions source payload width differs')
        values = dict(zip(source[kind].fields, cells, strict=True))
        if inverse and kind == 'memory_write_events' and variant == 'actual_no_memory_fk':
            values['attempt_id'] = db.execute('select jsonb(?)', (str(public_parent),)).fetchone()[0]
        values = defaults | values if kind == 'reply_attempts' else values
        return _framed((values[field] for field in target[kind].fields), width)
    db.create_function('ceo_current_actions_payload', 3, convert)
    parent_sql = ("cast((select p.legacy_key from actions p where p.id=a.memory_attempt_ref "
                  "and p.record_type='reply_attempts') as integer)" if inverse or variant == 'fresh_memory_fk'
                  else 'a.memory_attempt_id')
    if not inverse and variant == 'actual_no_memory_fk':
        if db.execute("select 1 from actions a where a.record_type='memory_write_events' and not exists "
                      "(select 1 from actions p where p.record_type='reply_attempts' "
                      "and cast(p.legacy_key as integer)=a.memory_attempt_id) limit 1").fetchone():
            raise ValueError('Memory public parent cannot be resolved')
    if inverse:
        from app.current_action_schema import source_current_action_objects
        objects = source_current_action_objects()
    else:
        objects = current_action_objects()
    source_cols = list(_columns(db, 'actions'))
    target_objects = current_action_objects() if not inverse else None
    if inverse:
        from app.current_action_schema import source_current_action_objects
        target_objects = source_current_action_objects()
    table_sql = next(obj['sql'] for obj in target_objects if obj['type'] == 'table')
    with closing(sqlite3.connect(':memory:')) as target_db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(target_db)
        target_db.execute(table_sql)
        target_cols = [row[1] for row in target_db.execute('pragma table_xinfo(actions)')
                       if row[6] == 0]
    cols = [column for column in source_cols if column in target_cols]
    cols.extend(column for column in target_cols if column not in cols)
    if not inverse and variant == 'actual_no_memory_fk':
        cols.append('memory_attempt_ref')
    if inverse and variant == 'actual_no_memory_fk':
        cols.remove('memory_attempt_ref')
    select = []
    for col in cols:
        if col == 'payload_json':
            select.append(f'ceo_current_actions_payload(a.record_type,a.payload_json,{parent_sql}) as payload_json')
        elif col not in source_cols:
            select.append('null as ' + _quote(col))
        elif col == 'memory_attempt_ref' and not inverse and variant == 'actual_no_memory_fk':
            select.append("(select p.id from actions p where a.record_type='memory_write_events' "
                          "and p.record_type='reply_attempts' and cast(p.legacy_key as integer)=a.memory_attempt_id) as memory_attempt_ref")
        else:
            select.append('a.' + _quote(col))
    db.execute(f'create temp table {STAGE} as select {",".join(select)} from main.actions a')
    db.execute(f'create temp table {WATER} as select rowid,name,seq from main.sqlite_sequence')
    db.execute('drop table main.actions')
    db.execute(next(obj['sql'] for obj in objects if obj['type'] == 'table'))
    names = ','.join(map(_quote, cols))
    db.execute(f'insert into main.actions({names}) select {names} from temp.{STAGE} order by id')
    for category in ('index', 'trigger'):
        for obj in objects:
            if obj['type'] == category:
                db.execute(obj['sql'])
    # All physical stored cells must survive the transfer, including binary JSONB.
    if db.execute(f'select {names} from main.actions except select {names} from temp.{STAGE} limit 1').fetchone():
        raise ValueError('Actions transfer changed stored facts')
    db.execute('delete from main.sqlite_sequence')
    db.execute(f'insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{WATER}')
    db.execute(f'drop table temp.{STAGE}')
    db.execute(f'drop table temp.{WATER}')
    db.create_function('ceo_current_actions_payload', 3, None)
    if _foreign_keys(db) != before_fk:
        raise ValueError('Actions conversion changed FK findings')


@dataclass
class CurrentActionSchemaRelease:
    root: Path
    database_path: Path
    original_version: tuple
    email_versions: tuple
    variant: str
    source_fk: tuple

    def _verify(self, db, *, target):
        marker, versions = _markers(db)
        expected = current_objects(email_ledger=True) if target else source_objects(self.variant)
        if (schema_objects(db) != expected or versions != self.email_versions
                or marker is None or (marker[0] != STORE_VERSION if target else marker != self.original_version)
                or _foreign_keys(db) != self.source_fk
                or db.execute('pragma quick_check').fetchone()[0] != 'ok'):
            raise ValueError('current Actions publication declaration/marker/integrity mismatch')

    def publish(self):
        self._convert(inverse=False)
        from app.store_schema_release import _migrate_store
        _migrate_store(self.root, self.database_path)
        self.verify_loaded()

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            try:
                if inverse and _markers(db)[0] == self.original_version:
                    self._verify(db, target=False)
                else:
                    self._verify(db, target=inverse)
                    _rebuild(db, self.variant, inverse=inverse)
                    update_state_rows(db, 'service_state', {
                        'value': self.original_version[0] if inverse else STORE_VERSION,
                        'updated_at': self.original_version[1] if inverse else datetime.now(timezone.utc).isoformat(),
                    }, where_sql='key=?', parameters=(VERSION_KEY,))
                    self._verify(db, target=not inverse)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def verify_loaded(self):
        require_current_pair(self.database_path)
        with closing(sqlite3.connect(self.database_path.as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('begin')
            self._verify(db, target=True)

    def rollback(self):
        self._convert(inverse=True)

    def finalize(self):
        self.verify_loaded()


def prepare_current_action_schema_release(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('begin')
        tables = {row[0] for row in db.execute("select name from sqlite_schema where type='table'")}
        if not {'state', 'email_schema_migrations'} <= tables:
            raise ValueError('current Actions release requires exact Store19.11/Email53')
        marker, versions = _markers(db)
        if marker is None or marker[0] != SOURCE_VERSION or not versions or versions[-1][0] != EMAIL_VERSION:
            raise ValueError('current Actions release requires exact Store19.11/Email53')
        variant = next((variant for variant in actions.REPLY_VARIANTS if schema_objects(db) == source_objects(variant)), None)
        if variant is None:
            raise ValueError('unsupported 19.11 source declaration')
        release = CurrentActionSchemaRelease(root, path, marker, versions, variant, _foreign_keys(db))
        release._verify(db, target=False)
        return release
