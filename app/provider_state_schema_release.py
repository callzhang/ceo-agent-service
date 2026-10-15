"""Source-bound provider-state forward and current-fact inverse.

The caller owns the transaction and release selection. No row snapshot is retained.
"""

import sqlite3

from app.action_email_storage import _external_dependencies
from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_storage import _no_inbound
from app.provider_state_definitions import (
    GENERATION_FIELDS, OBSERVATION_FIELDS, PROVIDER_STATE_INDEXES,
)
from app.provider_state_storage import (
    _generation_bind, _observation_bind, create_provider_state_schema,
)

OBS = "email_provider_observations"
GEN = "email_provider_folder_observation_generations"
TARGET = "provider_state"
SOURCE_TABLES = (OBS, GEN)
SOURCE_FINGERPRINTS = {
    "actual": {
        OBS: "852cac080d7f3c95647ffee82fac206894d04461a8558cd6c41e8ef5620e5179",
        GEN: "0759d174cab09f98a3720d771b3522c7eb5ed6a2c750b12397ba3cff65a2ec56",
    },
    "fresh": {
        OBS: "9bbf4d7f63d23aa1763200b7bb09ed2dc039de84a93460aba04a6e5eb7c2b017",
        GEN: "0759d174cab09f98a3720d771b3522c7eb5ed6a2c750b12397ba3cff65a2ec56",
    },
}
TARGET_FINGERPRINT = "4603f08b8194934df3ae2b37432001ea65c08fe5904586d4a812854fe657f687"
TARGET_INDEX_NAMES = tuple(ddl.split(" INDEX ", 1)[1].split(" ON ", 1)[0]
                           for ddl in PROVIDER_STATE_INDEXES)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _present(db, schema, name):
    return db.execute(
        f"select 1 from {schema}.sqlite_schema where name=? collate nocase limit 1",
        (name,),
    ).fetchone() is not None


def _no_temp_collisions(db, names):
    if any(_present(db, "temp", name) for name in names):
        raise ValueError("unsupported provider-state TEMP name collision")


def _require_transaction(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("provider-state migration requires caller transaction")


def _validate_metadata(metadata):
    if not isinstance(metadata, dict) or set(metadata) != {"variant", "tables"}:
        raise ValueError("unsupported provider-state migration metadata")
    variant = metadata["variant"]
    tables = metadata["tables"]
    if variant not in SOURCE_FINGERPRINTS or not isinstance(tables, dict) or set(tables) != set(SOURCE_TABLES):
        raise ValueError("unsupported provider-state migration metadata")
    if any(_fingerprint(tables[name]) != expected
           for name, expected in SOURCE_FINGERPRINTS[variant].items()):
        raise ValueError("unsupported provider-state migration metadata")


def _source_variant(db):
    declarations = {name: _source_declaration(db, name) for name in SOURCE_TABLES}
    hashes = {name: _fingerprint(declaration) for name, declaration in declarations.items()}
    variant = next((name for name, expected in SOURCE_FINGERPRINTS.items()
                    if hashes == expected), None)
    if variant is None:
        raise ValueError("unsupported provider-state source declarations")
    return variant, declarations


def capture_provider_state_source(db: sqlite3.Connection) -> dict:
    """Read only the exact actual/fresh declarations; never capture row bodies."""
    _no_temp_collisions(db, (*SOURCE_TABLES, TARGET, *TARGET_INDEX_NAMES))
    if any(_present(db, "main", name) for name in (TARGET, *TARGET_INDEX_NAMES)):
        raise ValueError("provider-state target name already exists")
    variant, tables = _source_variant(db)
    _no_inbound(db, SOURCE_TABLES)
    _external_dependencies(db, targets=SOURCE_TABLES, allowed_trigger_names=())
    return {"variant": variant, "tables": tables}


def _target_shape(db):
    if any(_present(db, "main", name) for name in SOURCE_TABLES):
        raise ValueError("provider-state source name remains in target")
    if _fingerprint(_source_declaration(db, TARGET)) != TARGET_FINGERPRINT:
        raise ValueError("unsupported provider-state target declaration")


def provider_state_schema_is_current(db: sqlite3.Connection) -> bool:
    """Recognize only the accepted target declaration without changing the DB."""
    try:
        _target_shape(db)
    except (ValueError, sqlite3.Error):
        return False
    return True


def _projection(table, fields, kind=None):
    values = [f"{_quote(field)}" for field in fields]
    values += [f"typeof({_quote(field)})" for field in fields]
    where = f" where record_type='{kind}'" if kind else ""
    return f"select {','.join(values)} from main.{_quote(table)}{where}"


def _same_facts(db, source, fields, kind):
    left = _projection(source, fields)
    right = _projection(TARGET, fields, kind)
    for first, second in ((left, right), (right, left)):
        if db.execute(f"select 1 from ({first} except {second}) limit 1").fetchone():
            raise ValueError(f"provider-state {kind} values or storage classes differ")
    source_count = db.execute(f"select count(*) from main.{_quote(source)}").fetchone()[0]
    target_count = db.execute(
        "select count(*) from main.provider_state where record_type=?", (kind,)
    ).fetchone()[0]
    if source_count != target_count:
        raise ValueError(f"provider-state {kind} row counts differ")


def apply_provider_state_migration(db: sqlite3.Connection, metadata: dict) -> None:
    """Copy all current source facts to the exact target under caller transaction."""
    _require_transaction(db)
    _validate_metadata(metadata)
    current = capture_provider_state_source(db)
    if current != metadata:
        raise ValueError("provider-state source declaration changed since capture")

    create_provider_state_schema(db)
    obs_sql = (
        "insert into main.provider_state "
        "(record_type,account_id,stable_message_identity,state,provider_folder_id,observed_at,payload_json) "
        "values('observation',?,?,?,?,?,?)"
    )
    obs_fields = ",".join(map(_quote, OBSERVATION_FIELDS))
    observations = db.execute(f"select {obs_fields} from main.{_quote(OBS)}")
    db.executemany(obs_sql, (_observation_bind(dict(zip(OBSERVATION_FIELDS, row, strict=True)))
                             for row in observations))
    gen_sql = (
        "insert into main.provider_state "
        "(record_type,account_id,provider_folder_id,observed_at,payload_json) "
        "values('folder_generation',?,?,?,'{}')"
    )
    gen_fields = ",".join(map(_quote, GENERATION_FIELDS))
    generations = db.execute(f"select {gen_fields} from main.{_quote(GEN)}")
    db.executemany(gen_sql, (_generation_bind(dict(zip(GENERATION_FIELDS, row, strict=True)))
                             for row in generations))
    _same_facts(db, OBS, OBSERVATION_FIELDS, "observation")
    _same_facts(db, GEN, GENERATION_FIELDS, "folder_generation")
    for name in SOURCE_TABLES:
        db.execute(f"drop table main.{_quote(name)}")
    _target_shape(db)


def rollback_provider_state_migration(db: sqlite3.Connection, metadata: dict) -> None:
    """Rebuild source declarations from current target facts, then remove target."""
    _require_transaction(db)
    _validate_metadata(metadata)
    source_indexes = tuple(index["name"] for definition in metadata["tables"].values()
                           for index in definition["indexes"] if index["sql"] is not None)
    _no_temp_collisions(db, (*SOURCE_TABLES, TARGET, *source_indexes))
    if any(_present(db, "main", name) for name in (*SOURCE_TABLES, *source_indexes)):
        raise ValueError("provider-state source name already exists")
    _target_shape(db)
    _no_inbound(db, (TARGET,))
    _external_dependencies(db, targets=(TARGET,), allowed_trigger_names=())

    for name in SOURCE_TABLES:
        definition = metadata["tables"][name]
        db.execute(definition["sql"])
        for index in definition["indexes"]:
            if index["sql"] is not None:
                db.execute(index["sql"])

    for name, fields, kind in (
        (OBS, OBSERVATION_FIELDS, "observation"),
        (GEN, GENERATION_FIELDS, "folder_generation"),
    ):
        selected = ",".join(map(_quote, fields))
        db.execute(
            f"insert into main.{_quote(name)} ({selected}) "
            f"select {selected} from main.provider_state where record_type=?",
            (kind,),
        )
        _same_facts(db, name, fields, kind)
    if {name: _source_declaration(db, name) for name in SOURCE_TABLES} != metadata["tables"]:
        raise ValueError("provider-state inverse declaration differs")
    db.execute("drop table main.provider_state")
