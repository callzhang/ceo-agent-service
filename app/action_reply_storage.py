"""Exact published Attempt/Memory preflight and opt-in paired action migration.

The source fingerprints come from the frozen Store16 actual/fresh declaration
inventory. A later migration must call this before any schema mutation.
"""

import hashlib
import json
import sqlite3

from app import action_storage as storage


SOURCE_TABLES = ("reply_attempts", "memory_write_events")
PUBLISHED_STORE_VERSION = "2026-10-13.4"
PUBLISHED_EMAIL_VERSION = 51
PUBLISHED_ACTIONS_FINGERPRINT = "2b2e7e5c152906c77938192856e3958b4c86c82df32727d1c845ca09301d13e0"
PUBLISHED_ACTION_TYPES = (
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_executions", "candidate_action_attempts",
    "external_action_results", "agent_effect_intents", "agent_execution_receipts",
    "outbound_postfixes", "outbound_postfix_receipts", "native_reply_dispatches",
    "sent_replies", "sent_reply_observers",
)
SOURCE_FINGERPRINTS = {
    "actual": {
        "reply_attempts": "5ec10b773b04a2a9c09d43fc8d9c01873c36b90c5c842b0ecb5e73857ea9f199",
        "memory_write_events": "4f236a93d6cba856697474a73f9541a8bcaf720e691139868d0e940f41490ec5",
    },
    "fresh_candidate17": {
        "reply_attempts": "3184d334f955b4e0b7be98f9cd22d149dd04b435976915c45461d4d5771c8fe1",
        "memory_write_events": "392cfe3a94e1a985f202a64a68bfdba92083e063ffb59929fb0f945cbf5220a2",
    },
}


def _source_declaration(db: sqlite3.Connection, table: str) -> dict:
    sql_row = db.execute("select sql from sqlite_schema where type='table' and name=? collate nocase",
                         (table,)).fetchone()
    quoted = '"' + table.replace('"', '""') + '"'
    columns = [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"), row,
                        strict=True)) for row in db.execute(f"pragma table_xinfo({quoted})")]
    foreign_keys = [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"),
                             row, strict=True)) for row in db.execute(f"pragma foreign_key_list({quoted})")]
    indexes = []
    for seq, name, unique, origin, partial in db.execute(f"pragma index_list({quoted})"):
        index_quoted = '"' + name.replace('"', '""') + '"'
        index_sql = db.execute("select sql from sqlite_schema where type='index' and name=?", (name,)).fetchone()
        indexes.append({
            "seq": seq, "name": name, "unique": unique, "origin": origin, "partial": partial,
            "columns": [dict(zip(("seqno", "cid", "name", "desc", "coll", "key"), row,
                                 strict=True)) for row in db.execute(f"pragma index_xinfo({index_quoted})")],
            "sql": index_sql[0] if index_sql else None,
        })
    triggers = [dict(zip(("name", "sql"), row, strict=True)) for row in db.execute(
        "select name,sql from sqlite_schema where type='trigger' and tbl_name=? collate nocase "
        "order by name", (table,))]
    return {"sql": sql_row[0] if sql_row else None, "columns": columns,
            "foreign_keys": foreign_keys, "indexes": indexes, "triggers": triggers}


def _fingerprint(declaration: object) -> str:
    encoded = json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def capture_reply_source(db: sqlite3.Connection) -> dict:
    """Validate and capture only source metadata inside the caller's read transaction."""
    marker = db.execute("select value from service_state where key='store_schema_version'").fetchone()
    if marker is None or marker[0] != PUBLISHED_STORE_VERSION:
        raise ValueError("unsupported published Store source marker")
    email = db.execute("select max(version) from email_schema_migrations").fetchone()[0]
    if email != PUBLISHED_EMAIL_VERSION:
        raise ValueError("unsupported published Email source marker")
    actions = storage._objects(db, "actions")
    legacy_names = db.execute(
        "select name from sqlite_schema where name collate nocase in ("
        + ",".join("?" for _ in PUBLISHED_ACTION_TYPES) + ") limit 1",
        PUBLISHED_ACTION_TYPES,
    ).fetchone()
    if _fingerprint(actions) != PUBLISHED_ACTIONS_FINGERPRINT or legacy_names:
        raise ValueError("unsupported fourteen-type actions source declaration")
    if db.execute("select 1 from actions where record_type in ('reply_attempts','memory_write_events') "
                  "limit 1").fetchone():
        raise ValueError("unsupported mixed Attempt/Memory actions source")
    if db.execute("select 1 from sqlite_sequence where name in "
                  "('actions:reply_attempts','actions:memory_write_events') limit 1").fetchone():
        raise ValueError("unsupported mixed Attempt/Memory allocator source")
    fingerprints = {table: _fingerprint(_source_declaration(db, table)) for table in SOURCE_TABLES}
    variant = next((name for name, expected in SOURCE_FINGERPRINTS.items()
                    if fingerprints == expected), None)
    if variant is None:
        raise ValueError("unsupported Attempt/Memory source declaration")
    source_tables = {"actions", *SOURCE_TABLES}
    storage._no_inbound(db, source_tables)
    storage._no_sql_dependencies(db, source_tables)
    sequences = {table: (row[0] if (row := db.execute(
        "select seq from sqlite_sequence where name=?", (table,)).fetchone()) else None)
                 for table in SOURCE_TABLES}
    counts = {table: db.execute(f"select count(*) from {table}").fetchone()[0]
              for table in SOURCE_TABLES}
    return {"variant": variant, "store_version": marker[0], "email_version": email,
            "fingerprints": fingerprints, "sequences": sequences, "counts": counts,
            "actions": actions}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _columns(db, table):
    return tuple(row[1] for row in db.execute(f"pragma table_xinfo({_quote(table)})") if row[6] == 0)


def _rows(db, table, columns):
    return [tuple(row) for row in db.execute(
        f"select {','.join(map(_quote, columns))} from {_quote(table)} order by id")]


def _types(db, table, columns):
    return [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(name) + ')' for name in columns)} "
        f"from {_quote(table)} order by id")]


def _sequence(db, name):
    rows = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or rows and (type(rows[0][0]) is not int or not 0 <= rows[0][0] < 2**63):
        raise ValueError("invalid action sequence metadata")
    return rows[0][0] if rows else None


def _set_sequence(db, name, value):
    db.execute("delete from sqlite_sequence where name=?", (name,))
    if value is not None:
        db.execute("insert into sqlite_sequence(name,seq) values(?,?)", (name, value))


def _base_sequences(db):
    names = (*storage.ACTION_SEQUENCE_NAMES.values(), storage.DELIVERY_SEQUENCE_NAME)
    return {name: _sequence(db, name) for name in names}


def _base_rows(db, columns):
    placeholders = ','.join('?' for _ in PUBLISHED_ACTION_TYPES)
    return [tuple(row) for row in db.execute(
        f"select {','.join(map(_quote, columns))} from actions "
        f"where record_type in ({placeholders}) order by id", PUBLISHED_ACTION_TYPES)]


def _base_types(db, columns):
    placeholders = ','.join('?' for _ in PUBLISHED_ACTION_TYPES)
    return [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(name) + ')' for name in columns)} "
        f"from actions where record_type in ({placeholders}) order by id", PUBLISHED_ACTION_TYPES)]


def _insert_rows(db, table, columns, rows):
    db.executemany(f"insert into {_quote(table)}({','.join(map(_quote, columns))}) "
                   f"values({','.join('?' for _ in columns)})", rows)


def _create_actions_for_raw_copy(db, *, reply_variant=None):
    """Install exact native DDL, then defer row-dependent triggers until copy ends."""
    if reply_variant is None:
        storage.create_action_schema(db, include_delivery=True)
    else:
        storage.create_action_schema(db, include_reply=True, reply_variant=reply_variant)
    triggers = [obj for obj in storage._objects(db, 'actions') if obj['type'] == 'trigger']
    for obj in triggers:
        db.execute(f"drop trigger {_quote(obj['name'])}")
    return triggers


def _finish_action_raw_copy(db, triggers):
    for obj in triggers:
        db.execute(obj['sql'])


def _check_replacement_names(db, expected, current):
    current_names = {storage.sqlite_schema_name(obj['name']) for obj in current}
    for obj in expected:
        name = storage.sqlite_schema_name(obj['name'])
        if name in current_names:
            continue
        if db.execute("select 1 from sqlite_schema where name=? collate nocase", (obj['name'],)).fetchone():
            raise ValueError("unsupported replacement object name collision")


def _source_rows(db, table):
    columns = _columns(db, table)
    return columns, _rows(db, table, columns)


def _target_rows(db, kind, variant):
    sql = storage.reply_action_projection(kind, variant)
    columns = tuple(row[0] for row in db.execute(f"select * from ({sql}) limit 0").description)
    return columns, [tuple(row) for row in db.execute(f"select * from ({sql}) order by id")]


def _target_types(db, kind, variant, columns):
    sql = storage.reply_action_projection(kind, variant)
    return [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(name) + ')' for name in columns)} "
        f"from ({sql}) order by id")]


def _expected_rows(kind, columns, rows):
    index = {field: offset for offset, field in enumerate(columns)}
    result = []
    for row in rows:
        current = list(row)
        if kind == 'reply_attempts':
            current[index['audit_tool_events_json']] = storage._audit_markers(
                current[index['audit_tool_events_json']])
        else:
            current[index['payload_json']] = '{}'
        result.append(tuple(current))
    return result


def _target_variant(source_variant):
    return 'actual_no_memory_fk' if source_variant == 'actual' else 'fresh_memory_fk'


def migrate_action_reply_schema(db):
    """Move exact Store18 legacy reply facts inside the caller's FK-off transaction."""
    storage._require_transaction(db)
    source = capture_reply_source(db)
    variant = _target_variant(source['variant'])
    target_objects = storage._current_objects(include_reply=True, reply_variant=variant)
    _check_replacement_names(db, target_objects, source['actions'])
    declarations = {table: _source_declaration(db, table) for table in SOURCE_TABLES}
    source_sequences = {table: _sequence(db, table) for table in SOURCE_TABLES}
    action_columns = _columns(db, 'actions')
    old_actions = _base_rows(db, action_columns)
    old_action_types = _base_types(db, action_columns)
    source_rows = {table: _source_rows(db, table) for table in SOURCE_TABLES}
    source_types = {table: _types(db, table, source_rows[table][0]) for table in SOURCE_TABLES}
    base_sequences = _base_sequences(db)
    for table in SOURCE_TABLES:
        db.execute(f"drop table {_quote(table)}")
    db.execute('drop table actions')
    triggers = _create_actions_for_raw_copy(db, reply_variant=variant)
    _insert_rows(db, 'actions', action_columns, old_actions)
    for name, value in base_sequences.items():
        _set_sequence(db, name, value)
    _finish_action_raw_copy(db, triggers)
    for table in SOURCE_TABLES:
        columns, rows = source_rows[table]
        for row in rows:
            storage.insert_action(db, table, dict(zip(columns, row, strict=True)))
        _set_sequence(db, storage.REPLY_SEQUENCE_NAMES[table], source_sequences[table])
        actual_columns, actual_rows = _target_rows(db, table, variant)
        expected_types = [list(row) for row in source_types[table]]
        retired = 'audit_tool_events_json' if table == 'reply_attempts' else 'payload_json'
        for row in expected_types:
            row[columns.index(retired)] = 'text'
        if actual_columns != columns or actual_rows != _expected_rows(table, columns, rows) or \
                _target_types(db, table, variant, columns) != [tuple(row) for row in expected_types]:
            raise ValueError(f'{table} migrated facts differ from source')
    if _base_rows(db, action_columns) != old_actions or \
            _base_types(db, action_columns) != old_action_types or _base_sequences(db) != base_sequences:
        raise ValueError('retained action facts or allocators differ from source')
    return {'source_variant': source['variant'], 'target_variant': variant,
            'source_tables': declarations, 'source_actions': source['actions']}


def _validate_inverse_state(state):
    if type(state) is not dict or set(state) != {
        'source_variant', 'target_variant', 'source_tables', 'source_actions'
    } or state['source_variant'] not in SOURCE_FINGERPRINTS or \
            state['target_variant'] != _target_variant(state['source_variant']) or \
            state['source_actions'] != storage._current_objects(include_delivery=True) or \
            type(state['source_tables']) is not dict or set(state['source_tables']) != set(SOURCE_TABLES):
        raise ValueError('unsupported reply inverse metadata')
    expected = SOURCE_FINGERPRINTS[state['source_variant']]
    if any(_fingerprint(state['source_tables'][table]) != expected[table]
           for table in SOURCE_TABLES):
        raise ValueError('unsupported reply inverse source declaration')


def restore_action_reply_schema(db, state):
    """Recreate exact legacy declarations from current finite facts."""
    storage._require_transaction(db)
    _validate_inverse_state(state)
    variant = state['target_variant']
    if not storage.action_schema_is_current(db, include_reply=True, reply_variant=variant):
        raise ValueError('unsupported sixteen-type actions declaration')
    _check_replacement_names(db, state['source_actions'], storage._objects(db, 'actions'))
    for table in SOURCE_TABLES:
        objects = [{'name': table}, *state['source_tables'][table]['indexes'],
                   *state['source_tables'][table]['triggers']]
        _check_replacement_names(db, objects, ())
    storage._no_inbound(db, {'actions'})
    storage._no_sql_dependencies(db, {'actions'})
    # Source physical columns are exactly the stored columns shared by both declarations.
    source_columns = tuple(col[1] for col in db.execute('pragma table_xinfo(actions)')
                           if col[6] == 0 and col[1] != 'memory_attempt_ref')
    old_actions = _base_rows(db, source_columns)
    old_action_types = _base_types(db, source_columns)
    base_sequences = _base_sequences(db)
    reply_sequences = {table: _sequence(db, storage.REPLY_SEQUENCE_NAMES[table])
                       for table in SOURCE_TABLES}
    current = {table: _target_rows(db, table, variant) for table in SOURCE_TABLES}
    current_types = {table: _target_types(db, table, variant, current[table][0])
                     for table in SOURCE_TABLES}
    if variant == 'fresh_memory_fk' and db.execute(
        "select 1 from actions c left join actions p on p.id=c.memory_attempt_ref "
        "and p.record_type='reply_attempts' where c.record_type='memory_write_events' "
        "and p.id is null limit 1").fetchone():
        raise ValueError('unsupported orphan fresh Memory action')
    db.execute('drop table actions')
    triggers = _create_actions_for_raw_copy(db)
    _insert_rows(db, 'actions', source_columns, old_actions)
    for name, value in base_sequences.items():
        _set_sequence(db, name, value)
    _finish_action_raw_copy(db, triggers)
    for table in SOURCE_TABLES:
        definition = state['source_tables'][table]
        db.execute(definition['sql'])
        for item in (*reversed(definition['indexes']), *definition['triggers']):
            if item['sql'] is not None:
                db.execute(item['sql'])
        columns, rows = current[table]
        _insert_rows(db, table, columns, rows)
        _set_sequence(db, table, reply_sequences[table])
        if _source_declaration(db, table) != definition or _rows(db, table, columns) != rows or \
                _types(db, table, columns) != current_types[table]:
            raise ValueError(f'{table} inverse differs from current facts')
    for name in storage.REPLY_SEQUENCE_NAMES.values():
        _set_sequence(db, name, None)
    if _base_rows(db, source_columns) != old_actions or \
            _base_types(db, source_columns) != old_action_types or _base_sequences(db) != base_sequences:
        raise ValueError('retained action facts or allocators differ after inverse')
    return {'counts': {table: len(current[table][1]) for table in SOURCE_TABLES}}
