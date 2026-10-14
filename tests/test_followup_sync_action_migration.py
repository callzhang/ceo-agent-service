"""Read-only source capture for the disabled follow-up/Todo migration."""

from contextlib import closing
import sqlite3

import pytest

from app.action_storage import create_action_schema
from app.action_followup_sync_storage import capture_followup_sync_source
from test_followup_sync_source import CAPTURE, NAMES


@pytest.fixture(params=("actual_no_memory_fk", "fresh_memory_fk"))
def source(request, tmp_path):
    variant = request.param
    path = tmp_path / "source.sqlite"
    frozen = CAPTURE["actual"] if variant == "actual_no_memory_fk" else CAPTURE["fresh_production_constructor"]["snapshot"]
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=on")
        for name in ("tasks", "runs", "email_classifications", "business_tasks", "business_task_signals",
                     "business_work_clusters", "business_anchors", "business_projects", "business_project_candidates",
                     "business_attention_items", "work_projects", "work_todos"):
            db.execute(f"create table {name}(id integer primary key, record_type text)")
        create_action_schema(db, include_reply=True, reply_variant=variant, include_email=True)
        for name in NAMES:
            table = frozen["tables"][name]
            db.execute(table["sql"])
            for index in reversed(table["indexes"]):
                if index["sql"]:
                    db.execute(index["sql"])
        external = frozen["external_schema_references"][0]
        db.execute(external["sql"])
        for obj in frozen["related_table_objects"]["business_legacy_links"]["objects"]:
            if obj["type"] == "index" and obj["sql"]:
                db.execute(obj["sql"])
        db.commit()
    return path, variant


def _read(path, row_factory=None):
    uri = f"file:{path}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = row_factory
    db.execute("pragma query_only=on")
    db.execute("begin")
    return db


def test_capture_both_variants_and_native_watermarks(source):
    path, variant = source
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("insert into work_updates(project_id,source_type,source_ref,summary) values(1,'s','r','body')")
        writer.execute("delete from work_updates")
        writer.execute("update main.sqlite_sequence set seq=500 where name='work_updates'")
        writer.execute("insert into main.sqlite_sequence(name,seq) values('follow_up_drafts',0)")
        writer.commit()
    for factory in (None, sqlite3.Row):
        with closing(_read(path, factory)) as db:
            before = db.in_transaction, db.execute("pragma foreign_keys").fetchone()[0], db.execute("pragma query_only").fetchone()[0]
            captured = capture_followup_sync_source(db)
            assert captured["source_variant"] == ("actual" if variant == "actual_no_memory_fk" else "fresh_candidate")
            assert len(captured["source_tables"]) == 9
            assert sum(len(table["columns"]) for table in captured["source_tables"].values()) == 150
            assert captured["sequences"]["work_updates"] == 500
            assert captured["sequences"]["follow_up_drafts"] == 0
            assert captured["sequences"]["business_task_follow_ups"] is None
            assert captured["counts"]["work_updates"] == 0
            assert len(captured["external"]["business_legacy_links"]["columns"]) == 12
            assert (db.in_transaction, db.execute("pragma foreign_keys").fetchone()[0], db.execute("pragma query_only").fetchone()[0]) == before


@pytest.mark.parametrize("mutation", (
    "alter table work_updates add column surprise text",
    "create index extra_work_update on work_updates(summary)",
    "create view extra_view as select * from work_updates",
    "create table extra_child(id integer references work_updates(id))",
    "create trigger extra_trigger after insert on work_updates begin select 1; end",
    "create table idx_actions_sync_business_task_dingtalk_links_0(id integer)",
))
def test_unsupported_main_source_refuses_without_writes(source, mutation):
    path, _ = source
    with closing(sqlite3.connect(path)) as writer:
        writer.execute(mutation)
        writer.commit()
    with closing(_read(path)) as db:
        before = db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall()
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)
        assert db.execute("select type,name,sql from main.sqlite_schema order by type,name").fetchall() == before


def test_temp_shadow_is_preserved_but_temp_dependency_refuses(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("create temp table work_updates(id integer)")
        db.execute("insert into temp.work_updates values(71)")
        db.commit()
        db.execute("pragma query_only=on")
        db.execute("begin")
        assert capture_followup_sync_source(db)["counts"]["work_updates"] == 0
        assert db.execute("select * from temp.work_updates").fetchall() == [(71,)]
        db.execute("pragma query_only=off")
        db.execute("create temp view dependent as select * from main.work_updates")
        db.execute("pragma query_only=on")
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)


def test_historical_main_owner_temp_trigger_survives_shadow_and_is_refused(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("create temp trigger dependent after insert on main.work_updates begin select 1; end")
        db.execute("create temp table work_updates(id integer)")
        db.commit()
        db.execute("pragma query_only=on")
        db.execute("begin")
        before = db.execute("select type,name,sql from temp.sqlite_schema order by type,name").fetchall()
        with pytest.raises(ValueError, match="unsupported"):
            capture_followup_sync_source(db)
        assert db.execute("select type,name,sql from temp.sqlite_schema order by type,name").fetchall() == before


def test_unknown_action_type_and_allocator_alias_refuse(source):
    path, _ = source
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma ignore_check_constraints=on")
        db.execute("insert into actions(record_type,legacy_key,payload_json) values('unknown', '1', '{}')")
        db.execute("pragma ignore_check_constraints=off")
        db.commit()
    with closing(_read(path)) as db:
        with pytest.raises(ValueError, match="unsupported mixed actions"):
            capture_followup_sync_source(db)
    with closing(sqlite3.connect(path)) as db:
        db.execute("delete from actions where record_type='unknown'")
        db.execute("insert into main.sqlite_sequence(name,seq) values('actions:unknown',7)")
        db.commit()
    with closing(_read(path)) as db:
        with pytest.raises(ValueError, match="unsupported source allocator alias"):
            capture_followup_sync_source(db)
