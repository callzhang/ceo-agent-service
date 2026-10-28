"""Explicit Store26.1/Email54 -> Store28.1/Email54 current-fact inverse."""
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3

from app.model_retirement_schema_source import store26_objects
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = "2026-10-26.1"
TARGET_STORE_VERSION = "2026-10-28.1"
EMAIL_VERSION = 54
VERSION_KEY = "store_schema_version"

RETIRED_INDEXES = (
    "idx_configs_binding_config_order",
    "idx_business_object_tasks_business_key_identity",
    "idx_feedback_processing_items_feedback_key_identity",
    "idx_feedback_processing_rounds_feedback",
    "idx_run_adopted_reviews_consumer",
    "idx_workbench_artifacts_id_identity",
    "idx_workbench_attachments_id_identity",
    "idx_codex_session_id",
    "idx_email_samples_id",
    "idx_setup_events_id",
)
_RETIRED_TRIGGERS = (
    "trg_configs_transition_duplicate", "trg_configs_transition_update",
    "trg_configs_transition_delete",
)
_REBUILT_COLUMNS = {
    "configs": ("transition_promotion_config_version", "transition_promotion_ref"),
    "run_entity_identity_map": ("allocator_highwater",),
}


@lru_cache(maxsize=1)
def retirement_target_objects():
    """Pinned release target, independent of future current declarations."""
    objects = {row[1]: row for row in store26_objects()}
    tables = json.loads((Path(__file__).parent / "schema_fixtures" /
                         "store28_1_retirement_tables.json").read_text())
    if set(tables) != set(_REBUILT_COLUMNS):
        raise ValueError("invalid Store28.1 frozen target tables")
    for name in (*RETIRED_INDEXES, *_RETIRED_TRIGGERS):
        del objects[name]
    for name, sql in tables.items():
        objects[name] = ("table", name, name, sql)
    result = tuple(sorted(objects.values(), key=lambda row: (row[0], row[1])))
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    if digest != "d70f359f2076f4ea4842247879d6c0d4ee0a2a5ebedf100f790d362adba4d1d3":
        raise ValueError("invalid Store28.1 frozen target catalog")
    return result


def _objects(db):
    return tuple(tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema "
        "where name not glob 'sqlite_*' order by type,name"))


def _marker(db):
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) where key=?",
        (VERSION_KEY,),
    ).fetchone()


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _require_neutral_retirements(db):
    if db.execute("select 1 from configs where record_type='email_model_mode_transitions' limit 1").fetchone():
        raise ValueError("cannot retire nonempty email_model_mode_transitions")
    if db.execute("select 1 from run_entity_identity_map where allocator_highwater is not null limit 1").fetchone():
        raise ValueError("cannot retire non-NULL allocator_highwater")


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _ordinary_columns(db, table):
    return tuple(row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(table)})") if row[6] == 0)


def _rebuild_table(db, table, *, target):
    """Transfer present native values before restoring this table's own guards."""
    catalog = retirement_target_objects() if target else store26_objects()
    definitions = tuple(row for row in catalog if row[2] == table)
    table_sql = next(row[3] for row in definitions if row[0] == "table")
    stage = "_store28_retirement_" + table
    if db.execute("select 1 from temp.sqlite_schema where name=?", (stage,)).fetchone():
        raise ValueError("Store28.1 transfer TEMP collision")
    old_columns = _ordinary_columns(db, table)
    fields = ",".join(_quote(name) for name in old_columns)
    db.execute(f"create temp table {_quote(stage)} as select rowid as _source_rowid,{fields} from main.{_quote(table)}")
    db.execute(f"drop table main.{_quote(table)}")
    db.execute(table_sql)
    new_columns = _ordinary_columns(db, table)
    retired = _REBUILT_COLUMNS[table]
    if target:
        if new_columns != tuple(name for name in old_columns if name not in retired):
            raise ValueError("Store28.1 changed an unrelated stored column")
    elif old_columns != tuple(name for name in new_columns if name not in retired):
        raise ValueError("Store28.1 inverse changed an unrelated stored column")
    projection = ",".join(_quote(name) if name in old_columns else "null" for name in new_columns)
    names = ",".join(_quote(name) for name in new_columns)
    db.execute(f"insert into main.{_quote(table)}(rowid,{names}) "
               f"select _source_rowid,{projection} from temp.{_quote(stage)} order by _source_rowid")
    for kind, _, _, sql in definitions:
        if kind != "table":
            db.execute(sql)
    db.execute(f"drop table temp.{_quote(stage)}")


def _convert_objects(db, *, inverse):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Store28.1 release requires FK-off transaction")
    if not inverse:
        _require_neutral_retirements(db)
    waters = tuple(db.execute("select rowid,name,seq from sqlite_sequence order by rowid"))
    for table in _REBUILT_COLUMNS:
        _rebuild_table(db, table, target=not inverse)
    source = {row[1]: row for row in store26_objects()}
    for name in RETIRED_INDEXES:
        row = source[name]
        if row[2] in _REBUILT_COLUMNS:
            continue
        db.execute(row[3] if inverse else f"drop index main.{_quote(name)}")
    # Rebuilding Configs replaces its sequence entry. Keep every current water,
    # including deleted-ID gaps, source-family waters, rowids and storage classes.
    db.execute("delete from sqlite_sequence")
    db.executemany("insert into sqlite_sequence(rowid,name,seq) values(?,?,?)", waters)


class ModelRetirementSchemaRelease:
    def __init__(self, path, *, source_marker, source_fk):
        self.path = Path(path).resolve()
        self.source_marker = tuple(source_marker)
        self.source_fk = tuple(source_fk)
        self.published = False

    def _verify(self, db, *, target):
        expected = retirement_target_objects() if target else store26_objects()
        marker = _marker(db)
        version = TARGET_STORE_VERSION if target else SOURCE_STORE_VERSION
        if (_objects(db) != expected or marker is None or marker[0] != version
                or (not target and tuple(marker) != self.source_marker)
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION
                or _foreign_keys(db) != self.source_fk):
            raise ValueError("Store28.1 release schema, marker or foreign-key mismatch")
        if not target:
            _require_neutral_retirements(db)
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Store28.1 release integrity mismatch")

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            register_task8_functions(db)
            db.execute("pragma foreign_keys=off")
            db.execute("begin immediate")
            try:
                self._verify(db, target=inverse)
                _convert_objects(db, inverse=inverse)
                marker = self.source_marker if inverse else (
                    TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                update_state_rows(db, "service_state", {"value": marker[0], "updated_at": marker[1]},
                                  where_sql="key=?", parameters=(VERSION_KEY,))
                self._verify(db, target=not inverse)
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._convert(inverse=False)

    def _verify_open(self, *, target):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            register_task8_functions(db)
            db.execute("begin")
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


def prepare_model_retirement_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        register_task8_functions(db)
        db.execute("begin")
        marker = _marker(db)
        if (_objects(db) != store26_objects() or marker is None
                or marker[0] != SOURCE_STORE_VERSION
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
            raise ValueError("unsupported Store26.1 / Email54 retirement source")
        _require_neutral_retirements(db)
        return ModelRetirementSchemaRelease(path, source_marker=marker, source_fk=_foreign_keys(db))
