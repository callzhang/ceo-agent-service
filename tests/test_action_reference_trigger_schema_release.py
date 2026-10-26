"""Retire the obsolete candidate public-ID guard without rewriting facts."""
from contextlib import closing
import sqlite3

import pytest

from app.current_schema_manifest import schema_objects
from app.store import AutoReplyStore
from app.state_storage import update_state_rows


def test_current_schema_reparses_and_action_public_id_update_compiles():
    from app.current_database_schema import create_current_database_schema

    with closing(sqlite3.connect(":memory:")) as db:
        create_current_database_schema(db)
        db.execute("alter table actions rename column td_lease_owner to renamed_lease_owner")
        db.execute("alter table work_summary_inputs rename column body_compacted to renamed_body_compacted")
        db.execute("explain update actions set legacy_key=legacy_key")


def _source(path):
    from app.action_reference_trigger_schema_release import RETIRED_TRIGGER_SQL, SOURCE_STORE_VERSION
    from app.action_entity_storage import create_action, append_action_receipt
    from app.signal_source_owner_schema_release import source_objects as store_252_source_objects
    from app.state_storage import insert_state
    from app.task8_storage import register_task8_functions

    with closing(sqlite3.connect(path)) as db, db:
        register_task8_functions(db)
        objects = store_252_source_objects()
        for category in ("table", "index", "trigger"):
            for kind, _name, _owner, sql in objects:
                if kind == category:
                    db.execute(sql)
        db.execute(RETIRED_TRIGGER_SQL)
        db.execute("begin")
        db.execute("insert into email_schema_migrations values(54,'original-email-clock')")
        insert_state(db, "service_state", {
            "key": "store_schema_version",
            "value": SOURCE_STORE_VERSION,
            "updated_at": "source-clock",
        })
        action = create_action(db, values={"operation": "test", "status": "done"})
        append_action_receipt(db, action["id"], values={"provider_result_json": '{"receipt":"original"}'})


def _facts(path):
    with closing(sqlite3.connect(path)) as db:
        result = {}
        for (table,) in db.execute("select name from sqlite_schema where type='table' order by name"):
            columns = [row[1] for row in db.execute(f'pragma table_xinfo("{table}")') if row[6] != 2]
            names = ','.join('"' + column + '"' for column in columns)
            condition = " where key is not 'store_schema_version'" if table == "state" else ""
            rows = db.execute(f'select {names} from "{table}"{condition} order by {names}').fetchall()
            result[table] = rows
        return result


def test_exact_release_inverse_preserves_new_actions_receipts_and_source_clock(tmp_path):
    from app.action_reference_trigger_schema_release import (
        RETIRED_TRIGGER_NAME, RETIRED_TRIGGER_SQL,
        prepare_action_reference_trigger_release,
    )
    from app.action_entity_storage import create_action, append_action_receipt
    from app.current_publication_sources import publication_task_sources
    from app.signal_source_owner_schema_release import source_objects as store_252_source_objects
    from app.store_schema_release import prepare_database_schema_release
    from pathlib import Path

    path = tmp_path / "current-facts.sqlite3"
    _source(path)
    before = _facts(path)
    store_252 = store_252_source_objects()
    source_251 = tuple(sorted(
        (*store_252, ("trigger", RETIRED_TRIGGER_NAME, "actions", RETIRED_TRIGGER_SQL)),
        key=lambda item: (item[0], item[1]),
    ))
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == source_251
        publication_task_sources(db)
    with pytest.raises(sqlite3.IntegrityError, match="explicit paired"):
        AutoReplyStore(path)
    source_bytes = path.read_bytes()
    with pytest.raises(ValueError, match="unsupported"):
        prepare_database_schema_release(Path(__file__).parents[1], path)
    assert path.read_bytes() == source_bytes

    release = prepare_action_reference_trigger_release(path)
    release.publish()
    release.verify_loaded()
    release.finalize()
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == store_252
    assert _facts(path) == before
    published_bytes = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="explicit paired"):
        AutoReplyStore(path)
    assert path.read_bytes() == published_bytes
    with closing(sqlite3.connect(path)) as db, db:
        from app.task8_storage import register_task8_functions
        register_task8_functions(db)
        db.execute("begin")
        action = create_action(db, values={"operation": "new-test", "status": "done"})
        append_action_receipt(db, action["id"], values={"provider_result_json": '{"receipt":"new"}'})
    current = _facts(path)
    release.rollback()
    assert _facts(path) == current
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == source_251
        assert db.execute("select value,updated_at from state where key='store_schema_version'").fetchone() == ("2026-10-25.1", "source-clock")
    release = prepare_action_reference_trigger_release(path)
    release.publish()
    release.finalize()
    assert _facts(path) == current
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == store_252
        assert db.execute("select value from state where key='store_schema_version'").fetchone() == ("2026-10-25.2",)


@pytest.mark.parametrize("damage", ["marker", "email", "missing_trigger", "changed_trigger"])
def test_release_rejects_non_exact_source_without_writing(tmp_path, damage):
    from app.action_reference_trigger_schema_release import prepare_action_reference_trigger_release

    path = tmp_path / "malformed.sqlite3"
    _source(path)
    with closing(sqlite3.connect(path)) as db, db:
        if damage == "marker":
            db.execute("begin")
            update_state_rows(db, "service_state", {"value": "unrelated"}, where_sql="key=?", parameters=("store_schema_version",))
        elif damage == "email":
            db.execute("update email_schema_migrations set version=55 where version=54")
        else:
            db.execute("drop trigger trg_actions_public_reference_update")
            if damage == "changed_trigger":
                db.execute("create trigger trg_actions_public_reference_update before update of legacy_key on actions begin select 1; end")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="unsupported"):
        prepare_action_reference_trigger_release(path)
    assert path.read_bytes() == before
