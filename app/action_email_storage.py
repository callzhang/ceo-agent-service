"""Exact source binding and isolated Email action forward/current-fact inverse.

The fingerprints are from the frozen thirteen-table actual/fresh source pair
and the reviewed sixteen-type E action declarations. They are candidate
contracts; publication must bind its actual immediate predecessor separately.
"""

import hashlib
import json
import sqlite3
from contextlib import closing

from app import action_storage as storage
from app.action_reply_storage import _source_declaration
from app.action_email_definitions import (
    EMAIL_AUTOINCREMENT_TYPES, EMAIL_COMPOSITE_KEYS, EMAIL_ROWID_TYPES,
    EMAIL_TYPES_ORDER, email_action_projection,
)


SOURCE_TABLES = (
    "email_action_plans", "email_actions", "email_action_attempts",
    "email_unsubscribe_cancellations", "email_unsubscribe_claims",
    "email_unsubscribe_continuations", "email_unsubscribe_effects",
    "email_unsubscribe_receipts", "email_unsubscribe_steps",
    "email_direct_retry_requests", "email_feedback_requests",
    "email_reply_dispatch_claims", "email_reply_receipts",
)
REPLACED_TABLES = ("actions", *SOURCE_TABLES)
EXTERNAL_TRIGGER_NAMES = (
    "trg_email_direct_action_blocks_account_delete",
    "trg_email_direct_action_blocks_account_update",
    "trg_email_direct_action_blocks_plan_switch",
    "trg_email_unsubscribe_blocks_account_delete",
    "trg_email_unsubscribe_blocks_account_update",
    "trg_email_unsubscribe_blocks_message_delete",
    "trg_email_unsubscribe_blocks_message_update",
    "trg_email_unsubscribe_blocks_plan_switch",
    "trg_email_reply_dispatch_blocks_account_delete",
    "trg_email_reply_dispatch_blocks_account_update",
    "trg_email_reply_dispatch_blocks_message_delete",
    "trg_email_reply_dispatch_blocks_plan_switch",
    "trg_email_reply_dispatch_blocks_thread_update",
)
SOURCE_PAIRS = {
    ("actual_no_memory_fk", "actual"): (
        "69691ed77f99cf5209e9e78086595cc20a25bd01aee6770a6c35091a7887ef0d",
        "fc8e0f26e3d063444193752ce6933f0febda68a60b11ed572829a74a8fe88f61",
    ),
    ("fresh_memory_fk", "fresh_candidate"): (
        "d741764c7ac77bce2c774e8518788044cea3b3a56aac3d09096a7cc07f4e7663",
        "4d05ce37c15dc8ceeea07980e1b58437c2a2ac2b3eae07014797b16dee794e78",
    ),
}
EXTERNAL_TRIGGERS_FINGERPRINT = "f16284f5ddc64ffdd9966bd1083479dc6770ef1247ab8e5318425f27cb2bdaf4"


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _sequence(db, name):
    rows = db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or rows and (type(rows[0][0]) is not int or not 0 <= rows[0][0] < 2**63):
        raise ValueError("unsupported Email sequence metadata")
    return rows[0][0] if rows else None


def _external_dependencies(db, targets=REPLACED_TABLES):
    """Probe MAIN and TEMP references with SQLite's own schema rewrite rules."""
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema "
        "where type in ('table','view','trigger') and sql is not null "
        "and name not glob 'sqlite_*' "
        "order by case type when 'table' then 0 when 'view' then 1 else 2 end,name"
    )]
    temp_objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from temp.sqlite_schema "
        "where type in ('table','view','index','trigger') and sql is not null "
        "and name not glob 'sqlite_*' order by rowid"
    )]
    virtual = {(row[0], storage.sqlite_schema_name(row[1])) for row in db.execute(
        "pragma table_list") if row[2] == "virtual"}
    allowed = {storage.sqlite_schema_name(name) for name in EXTERNAL_TRIGGER_NAMES}
    target_names = {storage.sqlite_schema_name(name) for name in targets}
    with closing(sqlite3.connect(":memory:")) as parser:
        for kind, name, owner, sql in objects:
            if kind == "trigger" and (storage.sqlite_schema_name(name) in allowed or
                                      storage.sqlite_schema_name(owner) in target_names):
                continue
            if kind == "table" and ("main", storage.sqlite_schema_name(name)) in virtual:
                columns = [row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(name)})")]
                sql = f"create table {_quote(name)} (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(sql)
        for kind, name, _owner, sql in temp_objects:
            if kind == "table" and ("temp", storage.sqlite_schema_name(name)) in virtual:
                columns = [row[1] for row in db.execute(f"pragma temp.table_xinfo({_quote(name)})")]
                sql = f"CREATE TABLE {_quote(name)} (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(_temp_create_sql(kind, sql))
        main_external = _schema_references(parser, "main", target_names, allowed)
        temp_external = _schema_references(parser, "temp", set(), set())
        names = {storage.sqlite_schema_name(row[1]) for row in objects + temp_objects}
        for table in targets:
            replacement = "_email_dependency_" + table
            while storage.sqlite_schema_name(replacement) in names:
                replacement += "_"
            names.add(storage.sqlite_schema_name(replacement))
            parser.execute("savepoint email_dependency_probe")
            try:
                parser.execute(f"alter table main.{_quote(table)} rename to {_quote(replacement)}")
                if (_schema_references(parser, "main", target_names, allowed) != main_external or
                        _schema_references(parser, "temp", set(), set()) != temp_external):
                    raise ValueError(f"unsupported Email SQL dependency on {table}")
            finally:
                parser.execute("rollback to email_dependency_probe")
                parser.execute("release email_dependency_probe")
            parser.execute("savepoint email_dependency_probe")
            try:
                parser.execute(f"drop table main.{_quote(table)}")
                if _schema_references(parser, "temp", set(), set()) != temp_external:
                    raise ValueError(f"unsupported Email TEMP dependency on {table}")
            finally:
                parser.execute("rollback to email_dependency_probe")
                parser.execute("release email_dependency_probe")


def _temp_create_sql(kind, sql):
    prefixes = {
        "table": ("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE "),
        "view": ("CREATE VIEW IF NOT EXISTS ", "CREATE VIEW "),
        "index": ("CREATE UNIQUE INDEX IF NOT EXISTS ", "CREATE INDEX IF NOT EXISTS ",
                  "CREATE UNIQUE INDEX ", "CREATE INDEX "),
        "trigger": ("CREATE TRIGGER IF NOT EXISTS ", "CREATE TRIGGER "),
    }
    for prefix in prefixes[kind]:
        if sql.startswith(prefix):
            return sql.replace(prefix, prefix + "temp.", 1)
    raise ValueError("unsupported TEMP schema declaration")


def _schema_references(db, schema, target_names, allowed):
    return {(schema, kind, storage.sqlite_schema_name(name)): sql
            for kind, name, owner, sql in db.execute(
                f"select type,name,tbl_name,sql from {schema}.sqlite_schema "
                "where type in ('table','view','index','trigger') and sql is not null "
                "and name not glob 'sqlite_*'")
            if kind in ("view", "trigger") and not (
                schema == "main" and kind == "trigger" and (
                    storage.sqlite_schema_name(name) in allowed or
                    storage.sqlite_schema_name(owner) in target_names))}


def capture_email_action_source(db: sqlite3.Connection) -> dict:
    """Capture only supported candidate source metadata in the caller's read transaction."""
    actions = storage._objects(db, "actions")
    action_fingerprint = _fingerprint(actions)
    declarations = {table: _source_declaration(db, table) for table in SOURCE_TABLES}
    email_fingerprint = _fingerprint(declarations)
    pair = next((pair for pair, hashes in SOURCE_PAIRS.items()
                 if hashes == (action_fingerprint, email_fingerprint)), None)
    if pair is None:
        raise ValueError("unsupported sixteen-type actions or Email source declaration pair")
    if db.execute("select 1 from main.actions where record_type not in (" +
                  ",".join("?" for _ in storage.ALL_ACTION_TYPES_WITH_REPLY) +
                  ") limit 1", storage.ALL_ACTION_TYPES_WITH_REPLY).fetchone():
        raise ValueError("unsupported mixed Email or unknown action record type")
    allowed_names = {storage.sqlite_schema_name(item["name"]) for item in actions}
    allowed_names.update(storage.sqlite_schema_name(name) for name in SOURCE_TABLES)
    allowed_names.update(storage.sqlite_schema_name(name) for name in EXTERNAL_TRIGGER_NAMES)
    for obj in storage._current_objects(include_reply=True, reply_variant=pair[0], include_email=True):
        name = storage.sqlite_schema_name(obj["name"])
        if name not in allowed_names and db.execute(
            "select 1 from sqlite_schema where name=? collate nocase", (obj["name"],)
        ).fetchone():
            raise ValueError("unsupported Email replacement name collision")
    external = [dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True)) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_schema where type='trigger' and name in (" +
        ",".join("?" for _ in EXTERNAL_TRIGGER_NAMES) + ") order by name",
        EXTERNAL_TRIGGER_NAMES,
    )]
    if _fingerprint(external) != EXTERNAL_TRIGGERS_FINGERPRINT:
        raise ValueError("unsupported external Email trigger declaration")
    storage._no_inbound(db, set(REPLACED_TABLES))
    _external_dependencies(db)
    allowed_sequences = set(storage.ACTION_SEQUENCE_NAMES.values()) | {
        storage.DELIVERY_SEQUENCE_NAME, *storage.REPLY_SEQUENCE_NAMES.values(),
    }
    if db.execute("select 1 from main.sqlite_sequence where name like 'actions:%' and name not in (" +
                  ",".join("?" for _ in allowed_sequences) + ") limit 1",
                  tuple(sorted(allowed_sequences))).fetchone():
        raise ValueError("unsupported action allocator alias")
    sequences = {name: _sequence(db, name) for name in (
        *SOURCE_TABLES, *sorted(allowed_sequences))}
    if any(sequences[name] is not None for name in SOURCE_TABLES
           if name not in EMAIL_AUTOINCREMENT_TYPES):
        raise ValueError("unsupported Email non-allocator sequence metadata")
    counts = {name: db.execute(f"select count(*) from main.{_quote(name)}").fetchone()[0]
              for name in SOURCE_TABLES}
    return {
        "action_variant": pair[0], "email_variant": pair[1],
        "source_actions": actions, "source_tables": declarations,
        "external_triggers": external, "sequences": sequences, "counts": counts,
        "locator_modes": {name: ("integer_id" if name in EMAIL_AUTOINCREMENT_TYPES else
                                  "composite" if name in storage.EMAIL_COMPOSITE_KEYS else "rowid")
                          for name in SOURCE_TABLES},
    }


def _columns(db, table):
    return tuple(row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(table)})")
                 if row[6] == 0)


def _rows(db, table, columns, *, rowid=False):
    names = (("rowid",) if rowid else ()) + columns
    order = "rowid" if rowid else "id" if "id" in columns else ",".join(EMAIL_COMPOSITE_KEYS[table])
    return [tuple(row) for row in db.execute(
        f"select {','.join(map(_quote, names))} from main.{_quote(table)} "
        f"order by {order}")]


def _types(db, table, columns, *, rowid=False):
    names = (("rowid",) if rowid else ()) + columns
    order = "rowid" if rowid else "id" if "id" in columns else ",".join(EMAIL_COMPOSITE_KEYS[table])
    return [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(name) + ')' for name in names)} "
        f"from main.{_quote(table)} order by {order}")]


def _set_sequence(db, name, value):
    db.execute("delete from main.sqlite_sequence where name=?", (name,))
    if value is not None:
        db.execute("insert into main.sqlite_sequence(name,seq) values(?,?)", (name, value))


def _insert_rows(db, table, columns, rows):
    db.executemany(f"insert into main.{_quote(table)}({','.join(map(_quote, columns))}) "
                   f"values({','.join('?' for _ in columns)})", rows)


def _target_external_sql():
    """The thirteen captured blockers, expressed against finite typed columns."""
    specs = (
        ("trg_email_direct_action_blocks_account_delete", "delete", "email_accounts", "",
         "email_actions", "a.email_account_id=old.account_id", "email_direct_action_in_flight"),
        ("trg_email_direct_action_blocks_account_update", "update", "email_accounts", "",
         "email_actions", "a.email_account_id=old.account_id", "email_direct_action_in_flight"),
        ("trg_email_direct_action_blocks_plan_switch", "update of status,current_action_plan_id",
         "email_classifications", "(old.status is not new.status or old.current_action_plan_id is not new.current_action_plan_id) and ",
         "email_actions", "a.email_classification_id=old.id", "email_direct_action_in_flight"),
        ("trg_email_unsubscribe_blocks_account_delete", "delete", "email_accounts", "",
         "email_unsubscribe_claims", "a.email_account_id=old.account_id", "email_unsubscribe_write_in_flight"),
        ("trg_email_unsubscribe_blocks_account_update", "update", "email_accounts", "",
         "email_unsubscribe_claims", "a.email_account_id=old.account_id", "email_unsubscribe_write_in_flight"),
        ("trg_email_unsubscribe_blocks_message_delete", "delete", "email_messages", "",
         "email_unsubscribe_claims", "a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity", "email_unsubscribe_write_in_flight"),
        ("trg_email_unsubscribe_blocks_message_update", "update of account_id,stable_message_identity,thread_identity", "email_messages",
         "(old.account_id is not new.account_id or old.stable_message_identity is not new.stable_message_identity or old.thread_identity is not new.thread_identity) and ",
         "email_unsubscribe_claims", "a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity", "email_unsubscribe_write_in_flight"),
        ("trg_email_unsubscribe_blocks_plan_switch", "update of status,current_action_plan_id", "email_classifications",
         "(old.status is not new.status or old.current_action_plan_id is not new.current_action_plan_id) and ",
         "email_unsubscribe_claims", "a.email_classification_id=old.id", "email_unsubscribe_write_in_flight"),
        ("trg_email_reply_dispatch_blocks_account_delete", "delete", "email_accounts", "",
         "email_reply_dispatch_claims", "a.email_account_id=old.account_id", "email_reply_dispatch_in_flight"),
        ("trg_email_reply_dispatch_blocks_account_update", "update", "email_accounts", "",
         "email_reply_dispatch_claims", "a.email_account_id=old.account_id", "email_reply_dispatch_in_flight"),
        ("trg_email_reply_dispatch_blocks_message_delete", "delete", "email_messages", "",
         "email_reply_dispatch_claims", "a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity", "email_reply_dispatch_in_flight"),
        ("trg_email_reply_dispatch_blocks_plan_switch", "update of current_action_plan_id", "email_classifications",
         "old.current_action_plan_id is not new.current_action_plan_id and ",
         "email_reply_dispatch_claims", "a.email_classification_id=old.id", "email_reply_dispatch_in_flight"),
        ("trg_email_reply_dispatch_blocks_thread_update", "update of account_id,stable_message_identity,thread_identity", "email_messages",
         "(old.account_id is not new.account_id or old.stable_message_identity is not new.stable_message_identity or old.thread_identity is not new.thread_identity) and ",
         "email_reply_dispatch_claims", "a.email_account_id=old.account_id and a.email_stable_message_identity=old.stable_message_identity", "email_reply_dispatch_in_flight"),
    )
    return {name: (f"CREATE TRIGGER {name} before {event} on main.{owner} when {changed}"
                   f"exists(select 1 from main.actions a where a.record_type='{kind}' and "
                   f"{matching} and a.email_status='{('processing' if kind == 'email_actions' else 'dispatching')}') "
                   f"begin select raise(abort,'{error}'); end")
            for name, event, owner, changed, kind, matching, error in specs}


def _projection(db, kind, variant):
    sql = email_action_projection(kind, include_rowid=kind in EMAIL_ROWID_TYPES,
                                  source_variant=variant).replace("from actions a", "from main.actions a")
    columns = tuple(item[0] for item in db.execute(f"select * from ({sql}) limit 0").description)
    rows = [tuple(row) for row in db.execute(f"select * from ({sql}) order by " +
                                            ("rowid" if kind in EMAIL_ROWID_TYPES else
                                             "id" if kind in EMAIL_AUTOINCREMENT_TYPES else
                                             ",".join(EMAIL_COMPOSITE_KEYS[kind])))]
    types = [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(name) + ')' for name in columns)} "
        f"from ({sql}) order by " +
        ("rowid" if kind in EMAIL_ROWID_TYPES else "id" if kind in EMAIL_AUTOINCREMENT_TYPES
         else ",".join(EMAIL_COMPOSITE_KEYS[kind])))]
    return columns, rows, types


def _action_rows(db, columns):
    rows = [tuple(row) for row in db.execute(
        f"select {','.join(map(_quote, columns))} from main.actions "
        f"where record_type not in ({','.join('?' for _ in EMAIL_TYPES_ORDER)}) order by id",
        EMAIL_TYPES_ORDER)]
    types = [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(name) + ')' for name in columns)} "
        "from main.actions where record_type not in "
        f"({','.join('?' for _ in EMAIL_TYPES_ORDER)}) order by id", EMAIL_TYPES_ORDER)]
    return rows, types


def _restore_triggers(db, triggers):
    for trigger in triggers:
        sql = trigger["sql"]
        if not sql.startswith("CREATE TRIGGER "):
            raise ValueError("unsupported captured trigger declaration")
        db.execute(sql.replace("CREATE TRIGGER ", "CREATE TRIGGER main.", 1))


def _create_main_index(db, sql):
    if sql.startswith("CREATE UNIQUE INDEX "):
        prefix = "CREATE UNIQUE INDEX "
    elif sql.startswith("CREATE INDEX "):
        prefix = "CREATE INDEX "
    else:
        raise ValueError("unsupported captured index declaration")
    db.execute(sql.replace(prefix, prefix + "main.", 1))


def _create_actions_for_copy(db, variant, *, email):
    objects = storage._current_objects(include_reply=True, reply_variant=variant,
                                       include_email=email)
    db.execute(next(item["sql"] for item in objects if item["type"] == "table"))
    for item in objects:
        if item["type"] == "index":
            _create_main_index(db, item["sql"])
    triggers = [item for item in objects if item["type"] == "trigger"]
    if storage._objects(db, "actions") != [item for item in objects if item["type"] != "trigger"]:
        raise ValueError("Email action raw-copy declaration differs from canonical")
    return triggers


def _insert_email_raw(db, kind, columns, row):
    values = dict(zip(columns, row, strict=True))
    if kind in EMAIL_ROWID_TYPES:
        locator = values.pop("rowid")
        key = str(locator)
    elif kind in EMAIL_AUTOINCREMENT_TYPES:
        key = str(values.pop("id"))
    else:
        identity = tuple(values.pop(field) for field in EMAIL_COMPOSITE_KEYS[kind])
        key = json.dumps([storage._encoded_sqlite_value(value) for value in identity],
                         ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    db.execute("insert into main.actions(record_type,legacy_key,payload_json) values(?,?,?)",
               (kind, key, storage._reply_payload(values)))


def migrate_email_action_schema(db: sqlite3.Connection) -> dict:
    """Move the frozen thirteen Email families in the caller's FK-off transaction."""
    storage._require_transaction(db)
    source = capture_email_action_source(db)
    variant = source["action_variant"]
    action_columns = _columns(db, "actions")
    old_rows, old_types = _action_rows(db, action_columns)
    old_sequences = source["sequences"]
    old_email = {}
    for kind in SOURCE_TABLES:
        columns = _columns(db, kind)
        rowid = kind in EMAIL_ROWID_TYPES
        old_email[kind] = (columns, _rows(db, kind, columns, rowid=rowid),
                           _types(db, kind, columns, rowid=rowid))
    for item in source["external_triggers"]:
        db.execute(f"drop trigger main.{_quote(item['name'])}")
    for kind in reversed(SOURCE_TABLES):
        db.execute(f"drop table main.{_quote(kind)}")
    db.execute("drop table main.actions")
    triggers = _create_actions_for_copy(db, variant, email=True)
    _insert_rows(db, "actions", action_columns, old_rows)
    for name, value in old_sequences.items():
        if name not in SOURCE_TABLES:
            _set_sequence(db, name, value)
    for kind in SOURCE_TABLES:
        columns, rows, types = old_email[kind]
        source_columns = (("rowid",) if kind in EMAIL_ROWID_TYPES else ()) + columns
        for row in rows:
            _insert_email_raw(db, kind, source_columns, row)
        projection_columns, projected, projected_types = _projection(db, kind, source["email_variant"])
        expected_columns = (("rowid",) if kind in EMAIL_ROWID_TYPES else ()) + columns
        if projection_columns != expected_columns or projected != rows or projected_types != types:
            raise ValueError(f"{kind} migrated facts differ from source")
    for kind in EMAIL_AUTOINCREMENT_TYPES:
        _set_sequence(db, f"actions:{kind}", old_sequences[kind])
    _restore_triggers(db, triggers)
    for sql in _target_external_sql().values():
        db.execute(sql)
    if storage._objects(db, "actions") != storage._current_objects(
        include_reply=True, reply_variant=variant, include_email=True
    ):
        raise ValueError("twenty-nine-type action declaration differs from canonical")
    if _action_rows(db, action_columns) != (old_rows, old_types):
        raise ValueError("retained action facts differ from source")
    if any(_sequence(db, name) != value for name, value in old_sequences.items()
           if name not in SOURCE_TABLES):
        raise ValueError("retained action allocators differ from source")
    return {"action_variant": variant, "email_variant": source["email_variant"],
            "source_actions": source["source_actions"],
            "source_tables": source["source_tables"],
            "external_triggers": source["external_triggers"]}


def _validate_inverse_state(state):
    if type(state) is not dict or set(state) != {
        "action_variant", "email_variant", "source_actions", "source_tables", "external_triggers"
    } or (state["action_variant"], state["email_variant"]) not in SOURCE_PAIRS or \
            _fingerprint(state["source_actions"]) != SOURCE_PAIRS[
                (state["action_variant"], state["email_variant"])][0] or \
            _fingerprint(state["source_tables"]) != SOURCE_PAIRS[
                (state["action_variant"], state["email_variant"])][1] or \
            _fingerprint(state["external_triggers"]) != EXTERNAL_TRIGGERS_FINGERPRINT:
        raise ValueError("unsupported Email inverse metadata")


def restore_email_action_schema(db: sqlite3.Connection, state: dict) -> dict:
    """Recreate frozen declarations from current twenty-nine-type facts."""
    storage._require_transaction(db)
    _validate_inverse_state(state)
    variant = state["action_variant"]
    if not storage.action_schema_is_current(db, include_reply=True,
                                            reply_variant=variant, include_email=True):
        raise ValueError("unsupported twenty-nine-type actions declaration")
    current_external = [dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True))
                        for row in db.execute(
                            "select type,name,tbl_name,sql from main.sqlite_schema "
                            "where type='trigger' and name in (" +
                            ",".join("?" for _ in EXTERNAL_TRIGGER_NAMES) + ") order by name",
                            EXTERNAL_TRIGGER_NAMES)]
    expected_external = _target_external_sql()
    if len(current_external) != 13 or any(
        item["sql"] != expected_external[item["name"]] for item in current_external
    ):
        raise ValueError("unsupported Email external trigger translation")
    expected = storage._current_objects(include_reply=True, reply_variant=variant)
    old_names = {storage.sqlite_schema_name(item["name"])
                 for item in storage._objects(db, "actions")}
    old_names.update(storage.sqlite_schema_name(item["name"]) for item in current_external)
    for obj in expected:
        if storage.sqlite_schema_name(obj["name"]) not in old_names and db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (obj["name"],)
        ).fetchone():
            raise ValueError("unsupported inverse replacement name collision")
    for kind, definition in state["source_tables"].items():
        names = [kind, *(item["name"] for item in definition["indexes"]),
                 *(item["name"] for item in definition["triggers"])]
        for name in names:
            if name and db.execute("select 1 from main.sqlite_schema where name=? collate nocase",
                                   (name,)).fetchone():
                raise ValueError("unsupported inverse source name collision")
    storage._no_inbound(db, {"actions"})
    _external_dependencies(db, ("actions",))
    allowed_sequences = set(storage.ACTION_SEQUENCE_NAMES.values()) | {
        storage.DELIVERY_SEQUENCE_NAME, *storage.REPLY_SEQUENCE_NAMES.values(),
        *(f"actions:{kind}" for kind in EMAIL_AUTOINCREMENT_TYPES),
    }
    if db.execute("select 1 from main.sqlite_sequence where name like 'actions:%' and name not in (" +
                  ",".join("?" for _ in allowed_sequences) + ") limit 1",
                  tuple(sorted(allowed_sequences))).fetchone():
        raise ValueError("unsupported inverse action allocator alias")
    action_columns = _source_action_columns(state)
    old_rows, old_types = _action_rows(db, action_columns)
    old_sequences = {name: _sequence(db, name) for name in (
        *storage.ACTION_SEQUENCE_NAMES.values(), storage.DELIVERY_SEQUENCE_NAME,
        *storage.REPLY_SEQUENCE_NAMES.values())}
    current_email = {kind: _projection(db, kind, state["email_variant"])
                     for kind in SOURCE_TABLES}
    email_sequences = {kind: _sequence(db, f"actions:{kind}") for kind in EMAIL_AUTOINCREMENT_TYPES}
    for item in current_external:
        db.execute(f"drop trigger main.{_quote(item['name'])}")
    db.execute("drop table main.actions")
    triggers = _create_actions_for_copy(db, variant, email=False)
    _insert_rows(db, "actions", action_columns, old_rows)
    for name, value in old_sequences.items():
        _set_sequence(db, name, value)
    _restore_triggers(db, triggers)
    for kind in SOURCE_TABLES:
        definition = state["source_tables"][kind]
        db.execute(definition["sql"])
        for index in reversed(definition["indexes"]):
            if index["sql"] is not None:
                _create_main_index(db, index["sql"])
    for kind in SOURCE_TABLES:
        columns, rows, types = current_email[kind]
        _insert_rows(db, kind, columns, rows)
        if kind in EMAIL_AUTOINCREMENT_TYPES:
            _set_sequence(db, kind, email_sequences[kind])
        if _rows(db, kind, columns[1:] if kind in EMAIL_ROWID_TYPES else columns,
                 rowid=kind in EMAIL_ROWID_TYPES) != rows or \
                _types(db, kind, columns[1:] if kind in EMAIL_ROWID_TYPES else columns,
                       rowid=kind in EMAIL_ROWID_TYPES) != types:
            raise ValueError(f"{kind} inverse differs from current facts")
    for kind in SOURCE_TABLES:
        _restore_triggers(db, state["source_tables"][kind]["triggers"])
    _restore_triggers(db, state["external_triggers"])
    for kind in EMAIL_AUTOINCREMENT_TYPES:
        _set_sequence(db, f"actions:{kind}", None)
    if _action_rows(db, action_columns) != (old_rows, old_types) or any(
        _sequence(db, name) != value for name, value in old_sequences.items()
    ):
        raise ValueError("retained action facts or allocators differ after inverse")
    if storage._objects(db, "actions") != state["source_actions"] or any(
        _source_declaration(db, kind) != state["source_tables"][kind] for kind in SOURCE_TABLES
    ):
        raise ValueError("restored source declaration differs from canonical")
    return {"counts": {kind: len(current_email[kind][1]) for kind in SOURCE_TABLES}}


def _source_action_columns(state):
    # The source action table is the one table object in the exact captured list.
    table = next(item for item in state["source_actions"] if item["type"] == "table")
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute(table["sql"])
        return tuple(row[1] for row in scratch.execute("pragma table_xinfo(actions)") if row[6] == 0)
