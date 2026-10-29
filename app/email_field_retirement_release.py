"""Explicit Store28.5 -> Store29.1 Email field retirement and current-data inverse."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sqlite3

from app.current_schema_manifest import current_objects, schema_objects, schema_is_current
from app.task_lifecycle_jsonb import _array, _framed
from app.task8_storage import register_task8_functions

SOURCE_STORE_VERSION = "2026-10-28.5"
TARGET_STORE_VERSION = "2026-10-29.1"
EMAIL_VERSION = 54
TABLES = ("actions", "emails", "email_classification_inputs")
ZERO_CELL = b"\x130"


@lru_cache(maxsize=1)
def source_objects():
    path = Path(__file__).with_name("schema_fixtures") / "store28_5_email54_catalog.json"
    return tuple(tuple(row) for row in json.loads(path.read_text()))


def _marker(db):
    from app.state_definitions import state_projection
    return db.execute(
        f"select value,updated_at from ({state_projection('service_state')}) "
        "where key='store_schema_version'"
    ).fetchone()


def _write_marker(db, version, updated_at):
    from app.state_storage import upsert_state
    upsert_state(db, "service_state", {"key": "store_schema_version", "value": version},
                 {"value": "json_quote(excluded.value)", "updated_at": "json_quote(?)"},
                 update_parameters=(updated_at,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _plan_payload(blob, *, inverse):
    width, cells = _array(blob)
    expected = 13 if inverse else 14
    if len(cells) != expected:
        raise ValueError("Email plan payload width differs from the exact release contract")
    if inverse:
        cells.insert(12, ZERO_CELL)
    else:
        if cells[12] != ZERO_CELL:
            raise ValueError("Email plan retirement requires the exact zero marker")
        del cells[12]
    return _framed(cells, width)


def _input_payload(raw, identity, *, inverse):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Email classification input is not an object")
    if value.get("input_compacted") is not True:
        return raw
    if inverse:
        if "stable_message_identity" in value:
            raise ValueError("Current compact input repeats its scalar identity")
        value["stable_message_identity"] = identity
    else:
        if value.pop("stable_message_identity", None) != identity:
            raise ValueError("Completed Email input identity differs from its scalar owner")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_source_data(db):
    if db.execute(
        "select 1 from emails where (classification_id is not null and "
        "class_legacy_processed_without_plan is not 0) or "
        "(classification_id is null and class_legacy_processed_without_plan is not null) limit 1"
    ).fetchone():
        raise ValueError("Classification retirement has nonneutral historical marker data")
    for (blob,) in db.execute("select payload_json from actions where record_type='email_action_plans'"):
        _plan_payload(blob, inverse=False)
    for channel, identity, raw in db.execute(
        "select channel,stable_message_identity,input_json from email_classification_inputs"
    ):
        if channel != "email":
            raise ValueError("Classification input channel is not Email")
        compact = _input_payload(raw, identity, inverse=False)
        if _input_payload(compact, identity, inverse=True) != raw:
            raise ValueError("Compact Email input is not exactly reconstructible")
    if db.execute("select 1 from tasks where kind='workbench_turn' "
                  "and status='waiting_confirmation' limit 1").fetchone():
        raise ValueError("Retired Workbench confirmation status still has data")
    if db.execute(
        "select 1 from task_inputs where json_valid(trigger_message_json) and "
        "json_extract(trigger_message_json,'$.lifecycle_version')="
        "'email_unsubscribe_consumer_direct_v1' limit 1"
    ).fetchone():
        raise ValueError("Retired direct unsubscribe lifecycle still has source records")


def _rebuild(db, *, inverse):
    objects = source_objects() if inverse else current_objects(email_ledger=True)
    table_sql = {name: sql for kind, name, _, sql in objects if kind == "table"}
    source_fields = {}
    for table in TABLES:
        stage = "_email_fields_" + table
        if db.execute("select 1 from temp.sqlite_schema where name=?", (stage,)).fetchone():
            raise ValueError("Email field transfer TEMP name already exists")
        fields = [row[1] for row in db.execute(f"pragma table_xinfo({_quote(table)})") if row[6] == 0]
        source_fields[table] = fields
        names = ",".join(map(_quote, fields))
        db.execute(f"create temp table {_quote(stage)}({names})")
        db.execute(f"insert into temp.{_quote(stage)} select {names} from main.{_quote(table)}")
    for table in TABLES:
        db.execute(f"drop table main.{_quote(table)}")
    for table in TABLES:
        db.execute(table_sql[table])
    db.create_function("ceo_retired_plan_payload", 1, lambda blob: _plan_payload(blob, inverse=inverse))
    db.create_function("ceo_retired_input_payload", 2,
                       lambda raw, identity: _input_payload(raw, identity, inverse=inverse))
    for table in TABLES:
        fields = [row[1] for row in db.execute(f"pragma table_xinfo({_quote(table)})") if row[6] == 0]
        retired = {"actions": set(), "emails": {"class_legacy_processed_without_plan"},
                   "email_classification_inputs": {"channel"}}[table]
        before, after = set(source_fields[table]), set(fields)
        expected_removed, expected_added = (set(), retired) if inverse else (retired, set())
        if before - after != expected_removed or after - before != expected_added:
            raise ValueError("Email retirement has an unmapped stored column")
        expressions = []
        for field in fields:
            if table == "actions" and field == "payload_json":
                expression = "case when _kind=64 then ceo_retired_plan_payload(payload_json) else payload_json end"
            elif table == "emails" and field == "class_legacy_processed_without_plan":
                expression = "case when classification_id is not null then 0 end"
            elif table == "email_classification_inputs" and field == "channel":
                expression = "'email'"
            elif table == "email_classification_inputs" and field == "input_json":
                expression = "ceo_retired_input_payload(input_json,stable_message_identity)"
            else:
                expression = _quote(field)
            expressions.append(expression)
        db.execute(f"insert into main.{_quote(table)}({','.join(map(_quote, fields))}) "
                   f"select {','.join(expressions)} from temp.{_quote('_email_fields_' + table)}")
        if (db.execute(f"select count(*) from main.{_quote(table)}").fetchone()[0]
                != db.execute(f"select count(*) from temp.{_quote('_email_fields_' + table)}").fetchone()[0]):
            raise ValueError("Email retirement changed a native owner row count")
    for kind, _, table, sql in objects:
        if table in TABLES and kind != "table":
            db.execute(sql)
    for table in TABLES:
        db.execute(f"drop table temp.{_quote('_email_fields_' + table)}")


@dataclass
class EmailFieldRetirementRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def _begin(self, db):
        register_task8_functions(db)
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")

    def publish(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                if (tuple(_marker(db) or ()) != self.source_marker
                        or schema_objects(db) != source_objects()
                        or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
                    raise ValueError("Email field retirement source changed after preparation")
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("Email field retirement source FK findings changed")
                _validate_source_data(db)
                _rebuild(db, inverse=False)
                _write_marker(db, TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())
                if (not schema_is_current(db) or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("Email field retirement target declaration or integrity differs")
                db.commit()
                self.published = True
            except BaseException:
                db.rollback()
                raise

    def verify_loaded(self):
        from app.current_schema_manifest import require_current_pair
        require_current_pair(self.database_path)

    def finalize(self):
        self.verify_loaded()

    def rollback(self):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            self._begin(db)
            try:
                if not self.published:
                    if (tuple(_marker(db) or ()) != self.source_marker or schema_objects(db) != source_objects()
                            or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
                        raise ValueError("Unpublished Email field release no longer has its exact source")
                    if _foreign_keys(db) != self.source_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
                        raise ValueError("Unpublished Email field source integrity differs")
                    db.rollback()
                    return
                marker = _marker(db)
                if (marker is None or marker[0] != TARGET_STORE_VERSION or not schema_is_current(db)
                        or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
                    raise ValueError("Email field inverse requires the exact published target")
                if _foreign_keys(db) != self.source_fk:
                    raise ValueError("Email field inverse target FK findings changed")
                _rebuild(db, inverse=True)
                _write_marker(db, self.source_marker[0], self.source_marker[1])
                if (schema_objects(db) != source_objects() or _foreign_keys(db) != self.source_fk
                        or db.execute("pragma quick_check").fetchone()[0] != "ok"):
                    raise ValueError("Email field inverse declaration or integrity differs")
                db.commit()
                self.published = False
            except BaseException:
                db.rollback()
                raise


def prepare_email_field_retirement_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        register_task8_functions(db)
        db.execute("begin")
        marker = _marker(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or schema_objects(db) != source_objects()
                or db.execute("select max(version) from email_schema_migrations").fetchone()[0] != EMAIL_VERSION):
            raise ValueError("Unsupported Store28.5 / Email54 retirement source")
        _validate_source_data(db)
        if db.execute("pragma quick_check").fetchone()[0] != "ok":
            raise ValueError("Email field retirement source integrity check failed")
        return EmailFieldRetirementRelease(path, tuple(marker), _foreign_keys(db))
