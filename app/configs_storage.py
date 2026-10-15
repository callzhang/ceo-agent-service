"""Unselected caller-transactional Email configs storage."""

from contextlib import closing
from dataclasses import dataclass
import json
import math
import sqlite3

from app.action_storage import _encoded_sqlite_value, _native_integer_primary_key
from app.configs_definitions import (
    CONFIG_FIELDS, CONFIG_INDEX_DDL, CONFIG_TABLE_DDL, CONFIG_TRIGGER_DDL,
    DEFAULTS, ROWID_KINDS, SCALAR_KEY, config_column, config_payload_fields,
)


@dataclass(frozen=True)
class ConfigWriteResult:
    lastrowid: int | None
    rowcount: int


_UNSET = object()


def create_configs_schema(db: sqlite3.Connection) -> None:
    """Install declarations only; caller owns transaction and schema selection."""
    for sql in (CONFIG_TABLE_DDL, *CONFIG_INDEX_DDL, *CONFIG_TRIGGER_DDL):
        db.execute(sql)


def _check(kind: str, values: dict) -> None:
    if kind not in CONFIG_FIELDS or set(values) - set(CONFIG_FIELDS[kind]):
        raise ValueError("unsupported Email config kind or source field")
    for value in values.values():
        _encoded_sqlite_value(value)


def _lock(db: sqlite3.Connection) -> None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("Email configs writes require a caller transaction")
    db.execute("update sqlite_sequence set seq=seq where 0")


def _source_bound_null(value) -> bool:
    return value is None or isinstance(value, float) and math.isnan(value)


def _text_affinity(value):
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute("create table source_key(value text)")
        scratch.execute("insert into source_key(value) values(?)", (value,))
        return scratch.execute("select value from source_key").fetchone()[0]


def _canonical_without_rowid_key(value) -> str:
    stored = _text_affinity(value)
    return json.dumps([_encoded_sqlite_value(stored)], ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


def _next_source_rowid(db: sqlite3.Connection, kind: str) -> int:
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from configs where record_type=?",
        (kind,),
    ).fetchone()[0]
    if maximum != 2**63 - 1:
        return 1 if maximum is None else maximum + 1
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute("create table source_rows(id integer primary key)")
        scratch.executemany("insert into source_rows(id) values(?)", db.execute(
            "select cast(legacy_key as integer) from configs where record_type=?",
            (kind,),
        ))
        return scratch.execute("insert into source_rows default values").lastrowid


def _completed(db: sqlite3.Connection, kind: str, supplied: dict) -> dict:
    fields = CONFIG_FIELDS[kind]
    expressions = [
        "?" if field in supplied else DEFAULTS.get(kind, {}).get(field, "null")
        for field in fields
    ]
    bindings = tuple(supplied[field] for field in fields if field in supplied)
    values = db.execute("select " + ",".join(expressions), bindings).fetchone()
    return dict(zip(fields, values, strict=True))


def _parent_id(db: sqlite3.Connection, kind: str, public_key) -> int:
    row = db.execute(
        "select id from configs where record_type=? and public_key=?",
        (kind, public_key),
    ).fetchone()
    return row[0] if row else 0


def _stored(db: sqlite3.Connection, kind: str, complete: dict,
            legacy_key: str) -> dict:
    payload = {
        field: _encoded_sqlite_value(complete[field])
        for field in config_payload_fields(kind)
    }
    data = {
        "record_type": kind, "legacy_key": legacy_key,
        "payload_json": json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":")),
    }
    if kind in SCALAR_KEY and kind in ROWID_KINDS:
        data["source_key"] = complete[SCALAR_KEY[kind]]
    if kind == "email_category_folder_bindings":
        data["binding_account_id"] = complete["account_id"]
        data["binding_category_key"] = complete["category_key"]
        data["binding_account_ref"] = _parent_id(
            db, "email_accounts", complete["account_id"])
        data["binding_category_ref"] = _parent_id(
            db, "email_category_configs", complete["category_key"])
    if kind == "email_model_mode_transitions":
        version = complete["promotion_config_version"]
        data["transition_promotion_config_version"] = version
        data["transition_promotion_ref"] = _parent_id(
            db, "email_model_promotion_configs", version)
    if kind == "email_training_snapshots":
        data["snapshot_snapshot_digest"] = complete["snapshot_digest"]
        data["snapshot_frozen"] = complete["frozen"]
    return data


def _identity(kind: str, identity) -> str:
    if kind in ROWID_KINDS:
        return str(_native_integer_primary_key(identity, update=True))
    return _canonical_without_rowid_key(identity)


def insert_config(db: sqlite3.Connection, kind: str, values: dict, *,
                  source_rowid=_UNSET, ignore=False) -> ConfigWriteResult:
    _check(kind, values)
    _lock(db)
    if kind in ROWID_KINDS:
        identity = (_next_source_rowid(db, kind)
                    if source_rowid is _UNSET or _source_bound_null(source_rowid)
                    else _native_integer_primary_key(source_rowid))
        legacy_key = str(identity)
    else:
        if source_rowid is not _UNSET:
            raise ValueError("WITHOUT ROWID config has no source rowid")
        legacy_key = _canonical_without_rowid_key(values.get(SCALAR_KEY[kind]))
        identity = None
    complete = _completed(db, kind, values)
    data = _stored(db, kind, complete, legacy_key)
    sql = ("insert " + ("or ignore " if ignore else "") + "into configs(" +
           ",".join(data) + ") values(" + ",".join("?" for _ in data) + ")")
    cursor = db.execute(sql, tuple(data.values()))
    return ConfigWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def update_config(db: sqlite3.Connection, kind: str, identity, values: dict, *,
                  new_source_rowid=_UNSET, ignore=False) -> int:
    _check(kind, values)
    _lock(db)
    key = _identity(kind, identity)
    physical = db.execute(
        "select id from configs where record_type=? and legacy_key=?",
        (kind, key),
    ).fetchone()
    if physical is None:
        return 0
    fields = CONFIG_FIELDS[kind]
    expression = ",".join(
        config_column(kind, field) + " as " + field for field in fields
    )
    cursor = db.execute(
        "select " + expression + " from configs where id=?", (physical[0],)
    )
    row = cursor.fetchone()
    current = dict(zip(fields, row, strict=True))
    current.update(values)
    if kind in ROWID_KINDS:
        legacy = (key if new_source_rowid is _UNSET else
                  str(_native_integer_primary_key(new_source_rowid, update=True)))
    else:
        if new_source_rowid is not _UNSET:
            raise ValueError("WITHOUT ROWID config has no source rowid")
        legacy = _canonical_without_rowid_key(current[SCALAR_KEY[kind]])
    data = _stored(db, kind, current, legacy)
    return db.execute(
        "update " + ("or ignore " if ignore else "") +
        "configs set " + ",".join(f"{field}=?" for field in data) +
        " where id=?", (*data.values(), physical[0]),
    ).rowcount


def delete_config(db: sqlite3.Connection, kind: str, identity) -> int:
    _check(kind, {})
    _lock(db)
    return db.execute(
        "delete from configs where record_type=? and legacy_key=?",
        (kind, _identity(kind, identity)),
    ).rowcount
