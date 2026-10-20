"""Unselected, reversible config/schedule schema release.

The caller owns an active FK-off transaction and its rollback. This module
changes declarations and current rows only; it never selects a runtime schema
version or stores a pre-migration row image.
"""

import sqlite3

from app.action_email_storage import _quote, _sequence, _set_sequence
from app.action_reply_storage import _fingerprint, _source_declaration
from app import action_storage
from app.configs_run_schema import (
    _declarations as _run_declarations, _objects as _run_objects,
    map_runs_to_configs, restore_native_run_fks,
)
from app.configs_schema_transfer import (
    SOURCE_TABLES, _metadata as _config_metadata, _native_rows,
    _preflight_bodies, _fields, _target_declarations, target_declaration_matches,
    capture_config_declarations, copy_config_rows, restore_config_rows,
)
from app.configs_storage import create_configs_schema
from app.schedules_storage import create_schedules_schema


_OBSERVATIONS = 'email_training_snapshot_observations'
_OBSERVATION_HASHES = {
    'actual': '002c18708d22a42212460b9d7c35b5dd5943a396386c5346a50aa06b4c87b587',
    'fresh': '7ad0a606a32b618f083378ec082ac9e92b6c69a4abae558dfcc0a380e9b5eb08',
}
_ACTION_VARIANTS = ('actual_no_memory_fk', 'fresh_memory_fk')
_SKILL_KINDS = ('managed_skill_export_receipts', 'runtime_skill_load_receipts')
_STAGE = '__configs_actions_stage'
_TEMP_NAMES = (_STAGE, 'configs', 'schedules', 'actions', 'runs', _OBSERVATIONS,
               *SOURCE_TABLES)


def _transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise sqlite3.ProgrammingError('config release requires caller FK-off transaction')


def _no_temp_names(db: sqlite3.Connection) -> None:
    placeholders = ','.join('?' for _ in _TEMP_NAMES)
    if db.execute('select 1 from temp.sqlite_schema where name collate nocase in ('
                  + placeholders + ') limit 1', _TEMP_NAMES).fetchone():
        raise ValueError('config release TEMP name collision')


def _actions(
    variant: str,
    *,
    skill: bool,
    include_followup_sync: bool = False,
    include_business_sync: bool = False,
    include_task_delivery: bool = False,
    include_errors: bool = False,
    include_feedback: bool = False,
    include_business_actions: bool = False,
    include_email_history: bool = False,
    inline_legacy_owner: bool = False,
    physical_provenance: bool = False,
    compact_payload: bool = False,
    selection_references: bool = False,
    result_references: bool = False, execution_owner: bool = False, sent_result_references: bool = False, shared_lifecycle: bool = False, specialized_claims: bool = False, postfix_owner: bool = False,
) -> list[dict]:
    return action_storage._current_objects(
        include_reply=True, reply_variant=variant, include_email=True,
        include_skill_config_receipts=skill,
        include_followup_sync=include_followup_sync,
        include_business_sync=include_business_sync,
        include_task_delivery=include_task_delivery,
        include_errors=include_errors,
        include_feedback=include_feedback,
        include_business_actions=include_business_actions,
        include_email_history=include_email_history,
        inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload,
        selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner,
    )


def _supported_observation(metadata: dict) -> bool:
    """Accept either frozen observation declaration independently of config layout."""
    return _fingerprint(metadata) in _OBSERVATION_HASHES.values()


def _state(metadata: dict) -> None:
    if type(metadata) is not dict or set(metadata) != {'configs', 'action_variant', 'observations'}:
        raise ValueError('unsupported config release metadata')
    _config_metadata(metadata['configs'])
    if metadata['action_variant'] not in _ACTION_VARIANTS:
        raise ValueError('unsupported action source variant')
    if not _supported_observation(metadata['observations']):
        raise ValueError('unsupported observation source declaration')


def _foreign_key_children(db: sqlite3.Connection, parents: set[str]) -> None:
    """Only the two translated runs edges and retired observation may escape."""
    normalize = action_storage.sqlite_schema_name
    source = {normalize(name) for name in (*SOURCE_TABLES, _OBSERVATIONS)}
    parents = {normalize(name) for name in parents}
    allowed = {
        ('runs', 'scheduled_tasks', 'scheduled_task_id', 'id'),
        ('runs', 'scheduled_task_config_versions', 'snapshot_id', 'id'),
    }
    for (child,) in db.execute("select name from main.sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        child_name = normalize(child)
        if child_name in source:
            continue
        for edge in db.execute(f'pragma main.foreign_key_list({_quote(child)})'):
            parent_name = normalize(edge[2])
            if parent_name in parents and (child_name, parent_name, edge[3], edge[4]) not in allowed:
                raise ValueError('unsupported external config foreign key')


def _target_foreign_key_children(db: sqlite3.Connection) -> None:
    normalize = action_storage.sqlite_schema_name
    for (child,) in db.execute("select name from main.sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        if normalize(child) in {'actions', 'runs', 'configs', 'schedules'}:
            continue
        if any(normalize(edge[2]) in {'configs', 'schedules'} for edge in db.execute(
                f'pragma main.foreign_key_list({_quote(child)})')):
            raise ValueError('unsupported external config target foreign key')


def capture_config_source(db: sqlite3.Connection) -> dict:
    """Bind exact native declarations and selected Email29 action shape."""
    _transaction(db)
    configs = capture_config_declarations(db)
    actions = action_storage._objects(db, 'actions')
    variant = next((name for name in _ACTION_VARIANTS if actions == _actions(name, skill=False)), None)
    if variant is None or not action_storage.action_schema_is_current(
            db, include_reply=True, reply_variant=variant, include_email=True):
        raise ValueError('unsupported 29-kind actions source declaration')
    if _run_objects(db) != _run_declarations(mapped=False):
        raise ValueError('unsupported native runs declaration')
    observation = _source_declaration(db, _OBSERVATIONS)
    if not _supported_observation(observation):
        raise ValueError('unsupported observation source declaration')
    if db.execute(f'select 1 from main.{_quote(_OBSERVATIONS)} limit 1').fetchone():
        raise ValueError('nonempty training observations cannot be retired')
    if any(_source_declaration(db, name)['sql'] is not None for name in ('configs', 'schedules')):
        raise ValueError('partial config target already exists')
    _no_temp_names(db)
    _foreign_key_children(db, set(SOURCE_TABLES) | {_OBSERVATIONS})
    action_storage._no_sql_dependencies(db, set(SOURCE_TABLES) | {_OBSERVATIONS})
    return {'configs': configs, 'action_variant': variant, 'observations': observation}


def _stored_columns(db: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(row[1] for row in db.execute('pragma main.table_xinfo(actions)') if row[6] == 0)


def _waters(db: sqlite3.Connection) -> dict[str, int]:
    return dict(db.execute("select name,seq from main.sqlite_sequence "
                           "where name='actions' or name like 'actions:%'"))


def _compare_stage(db: sqlite3.Connection, columns: tuple[str, ...]) -> None:
    predicate = ','.join('?' for _ in _SKILL_KINDS)
    count = db.execute(f'select count(*) from temp.{_quote(_STAGE)}').fetchone()[0]
    current = db.execute(f'select count(*) from main.actions where record_type not in ({predicate})',
                         _SKILL_KINDS).fetchone()[0]
    if count != current:
        raise ValueError('retained action row count changed')
    differences = ' or '.join(
        f'not (s.{_quote(name)} is a.{_quote(name)}) '
        f'or typeof(s.{_quote(name)})<>typeof(a.{_quote(name)})'
        for name in columns
    )
    if db.execute(f'select 1 from temp.{_quote(_STAGE)} s '
                  f'left join main.actions a on a.id=s.id '
                  f'where a.id is null or ({differences}) limit 1').fetchone():
        raise ValueError('retained action value or storage class changed')


def _rebuild_actions(db: sqlite3.Connection, variant: str, *, to_skill: bool) -> None:
    expected = _actions(variant, skill=not to_skill)
    if action_storage._objects(db, 'actions') != expected:
        raise ValueError('unsupported actions declaration')
    if db.execute('select 1 from temp.sqlite_schema where name=? collate nocase',
                  (_STAGE,)).fetchone():
        raise ValueError('actions TEMP name collision')
    columns = _stored_columns(db) if to_skill else tuple(
        item[1] for item in _source_action_columns(variant))
    # Inverse uses the source's stored column order, including every existing
    # action field and physical ID; Skill-only fields are omitted.
    names = ','.join(map(_quote, columns))
    predicate = ','.join('?' for _ in _SKILL_KINDS)
    db.execute(f'create temp table {_quote(_STAGE)} as select {names} from main.actions '
               f'where record_type not in ({predicate})', _SKILL_KINDS)
    _compare_stage(db, columns)
    waters = _waters(db)
    db.execute('drop table main.actions')
    replacement = _actions(variant, skill=to_skill)
    db.execute(next(item['sql'] for item in replacement if item['type'] == 'table'))
    for item in replacement:
        if item['type'] == 'index':
            db.execute(item['sql'])
    db.execute(f'insert into main.actions({names}) select {names} from temp.{_quote(_STAGE)}')
    _compare_stage(db, columns)
    for item in replacement:
        if item['type'] == 'trigger':
            db.execute(item['sql'])
    for name, value in waters.items():
        _set_sequence(db, name, value)
    if not to_skill:
        for kind in _SKILL_KINDS:
            _set_sequence(db, 'actions:' + kind, None)
    if action_storage._objects(db, 'actions') != replacement:
        raise ValueError('rebuilt actions declaration differs')
    if any(_sequence(db, name) != value for name, value in waters.items()
           if to_skill or name not in {'actions:' + kind for kind in _SKILL_KINDS}):
        raise ValueError('retained action allocator changed')
    db.execute(f'drop table temp.{_quote(_STAGE)}')


def _source_action_columns(variant: str) -> tuple[tuple, ...]:
    # This reads only a finite canonical declaration, never source rows.
    with sqlite3.connect(':memory:') as scratch:
        for item in _actions(variant, skill=False):
            if item['type'] == 'table':
                scratch.execute(item['sql'])
                break
        return tuple(row for row in scratch.execute('pragma main.table_xinfo(actions)') if row[6] == 0)


def _create_native(db: sqlite3.Connection, metadata: dict) -> None:
    for declaration in metadata['configs']['tables'].values():
        db.execute(declaration['sql'])
    db.execute(metadata['observations']['sql'])
    for declaration in (*metadata['configs']['tables'].values(), metadata['observations']):
        for index in reversed(declaration['indexes']):
            if index['sql'] is not None:
                db.execute(index['sql'])
        for trigger in declaration['triggers']:
            db.execute(trigger['sql'])
    if capture_config_declarations(db) != metadata['configs'] or \
            _source_declaration(db, _OBSERVATIONS) != metadata['observations']:
        raise ValueError('native declaration reconstruction differs')


def configs_content_schema_is_current(
    db: sqlite3.Connection,
    *,
    action_variant: str,
    include_followup_sync: bool = False,
    include_business_sync: bool = False,
    include_task_delivery: bool = False,
    include_errors: bool = False,
    include_feedback: bool = False,
    include_business_actions: bool = False,
    include_email_history: bool = False,
    canonical_email: bool = False,
    inline_legacy_owner: bool = False,
    physical_provenance: bool = False,
    compact_payload: bool = False,
    selection_references: bool = False,
    result_references: bool = False, execution_owner: bool = False, sent_result_references: bool = False, shared_lifecycle: bool = False, specialized_claims: bool = False, postfix_owner: bool = False,
) -> bool:
    """Read-only config/action predicate, independent of the Runs source profile."""
    if action_variant not in _ACTION_VARIANTS:
        raise ValueError('unsupported action declaration variant')
    expected_actions = _actions(
        action_variant, skill=True,
        include_followup_sync=include_followup_sync,
        include_business_sync=include_business_sync,
        include_task_delivery=include_task_delivery,
        include_errors=include_errors,
        include_feedback=include_feedback,
        include_business_actions=include_business_actions,
        include_email_history=include_email_history,
        inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload,
        selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner,
    )
    if canonical_email:
        expected_actions = [({**obj, 'sql': obj['sql'].replace(
            'references email_classifications(id)', 'references emails(classification_id)'
        )} if obj['type'] == 'table' else obj) for obj in expected_actions]
    return (all(target_declaration_matches(db, kind, declaration)
                for kind, declaration in _target_declarations().items())
            and action_storage._objects(db, 'actions') == expected_actions
            and action_storage.action_schema_is_current(
                db, include_reply=True, reply_variant=action_variant,
                include_email=True, include_skill_config_receipts=True,
                include_followup_sync=include_followup_sync,
                include_business_sync=include_business_sync,
                include_task_delivery=include_task_delivery,
                include_errors=include_errors,
                include_feedback=include_feedback,
                include_business_actions=include_business_actions,
                include_email_history=include_email_history,
                canonical_email=canonical_email,
                inline_legacy_owner=inline_legacy_owner,
                physical_provenance=physical_provenance, compact_payload=compact_payload,
                selection_references=selection_references, result_references=result_references, execution_owner=execution_owner, sent_result_references=sent_result_references, shared_lifecycle=shared_lifecycle, specialized_claims=specialized_claims, postfix_owner=postfix_owner)
            and all(_source_declaration(db, kind)['sql'] is None
                    for kind in (*SOURCE_TABLES, _OBSERVATIONS)))


def configs_schema_is_current(db: sqlite3.Connection, *, action_variant: str) -> bool:
    """Original mapped Runs8 contract for the historical Config release."""
    return (configs_content_schema_is_current(db, action_variant=action_variant)
            and _run_objects(db) == _run_declarations(mapped=True))


def migrate_config_schema(db: sqlite3.Connection, metadata: dict) -> dict[str, int]:
    """Retire eighteen native families plus the exact empty observation table."""
    _transaction(db)
    _state(metadata)
    if capture_config_source(db) != metadata:
        raise ValueError('config source changed after capture')
    rows = _native_rows(db, metadata['configs'])
    facts = {kind: [dict(zip(_fields(metadata['configs'], kind), row, strict=True))
                    for row in records] for kind, records in rows.items()}
    _preflight_bodies(facts)
    create_schedules_schema(db)
    create_configs_schema(db)
    _rebuild_actions(db, metadata['action_variant'], to_skill=True)
    counts = copy_config_rows(db, db, metadata['configs'])
    map_runs_to_configs(db)
    db.execute(f'drop table main.{_quote(_OBSERVATIONS)}')
    for kind in reversed(SOURCE_TABLES):
        db.execute(f'drop table main.{_quote(kind)}')
    if any(_source_declaration(db, kind)['sql'] is not None
           for kind in (*SOURCE_TABLES, _OBSERVATIONS)):
        raise ValueError('native config source remains after release')
    return counts


def restore_config_schema(db: sqlite3.Connection, metadata: dict) -> dict[str, int]:
    """Restore native declarations from current config, schedule and Skill facts."""
    _transaction(db)
    _state(metadata)
    if any(_source_declaration(db, kind)['sql'] is not None
           for kind in (*SOURCE_TABLES, _OBSERVATIONS)):
        raise ValueError('native config source already exists')
    if any(not target_declaration_matches(db, kind, declaration)
           for kind, declaration in _target_declarations().items()):
        raise ValueError('unsupported config target declaration')
    if action_storage._objects(db, 'actions') != _actions(metadata['action_variant'], skill=True):
        raise ValueError('unsupported 31-kind actions target declaration')
    if _run_objects(db) != _run_declarations(mapped=True):
        raise ValueError('unsupported mapped runs declaration')
    _no_temp_names(db)
    _target_foreign_key_children(db)
    action_storage._no_sql_dependencies(db, {'configs', 'schedules'})
    _create_native(db, metadata)
    counts = restore_config_rows(db, db, metadata['configs'])
    restore_native_run_fks(db)
    _rebuild_actions(db, metadata['action_variant'], to_skill=False)
    db.execute('drop table main.configs')
    db.execute('drop table main.schedules')
    for kind in metadata['configs']['auto_kinds']:
        if kind not in ('scheduled_tasks', *_SKILL_KINDS):
            _set_sequence(db, 'configs:' + kind, None)
    return counts
