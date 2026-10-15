"""Finite four-kind Business schema release against native SQLite."""

from contextlib import closing
import json
from pathlib import Path
import math
import sqlite3

import pytest

from app.business_storage import create_business_schema

FRESH_DECLARATIONS = Path(__file__).parent / "fixtures/business_four_kind_fresh_declarations.json"
SOURCE_ORACLE = (Path(__file__).parents[1] / "docs/superpowers/specs/"
                 "2026-10-15-database-redesign-business-core-native-source.json")


def _source_declarations(variant):
    source = json.loads(FRESH_DECLARATIONS.read_text())
    if variant == "actual":
        actual = json.loads(SOURCE_ORACLE.read_text())["actual"]
        for name in ("business_tasks", "business_projects", "business_attention_items"):
            section = "parents" if name in source["parents"] else "children"
            oracle_table = actual["tables"].get(name, actual["external_endpoint_tables"].get(name))
            source[section][name]["sql"] = oracle_table["sql"]
        source["variant"] = "actual"
    return source


def _source_db(variant):
    from app.business_schema_release import _recreate_source

    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=off")
    _recreate_source(db, _source_declarations(variant))
    for name in ("business_task_signals", "business_work_clusters", "work_projects",
                 "work_todos", "work_updates"):
        db.execute(f"create table {name}(id integer primary key)")
        db.execute(f"insert into {name}(id) values(-31)")
    db.commit()
    return db


def _seed_complete_source(db):
    db.execute("insert into business_tasks(id,title,stage,status,merged_into_task_id,"
               "owner_name) values(-11,'cycle a','candidate','merged',-12,?)", (b"\x00\xff",))
    db.execute("insert into business_tasks(id,title,stage,status,merged_into_task_id) "
               "values(-12,'cycle b','candidate','merged',-11)")
    db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
               "values(-11,'project','anchor a','anchor a')")
    db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
               "values(-21,'project','anchor b','anchor b')")
    db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
               "values(-11,-11,'project a','test')")
    db.execute("insert into business_project_context_revisions(id,project_id,context_json,"
               "evidence_json) values(-11,-11,'{}','[]')")
    db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
               "values(-21,-21,'project b','test')")
    db.execute("insert into business_attention_items(id,stable_key,category,title,"
               "why_attention,current_state,ceo_action,anchor_id,evidence_signal_id) "
               "values(-41,'attention','fyi','attention','why','state','action',-11,-31)")
    db.execute("insert into business_project_candidates(id,cluster_id,title,reason,status,"
               "confirmed_project_id,confirmation_signal_id) "
               "values(-42,-31,'candidate','reason','confirmed',-11,-31)")
    db.execute("insert into business_task_evidence(rowid,task_id,signal_id,evidence_role) "
               "values(81,-11,-31,'discovery')")
    db.execute("insert into business_project_evidence(rowid,project_id,signal_id) "
               "values(82,-11,-31)")
    db.execute("insert into business_task_relations(rowid,from_task_id,to_task_id,"
               "relation_type,supporting_signal_id) values(83,-11,-12,'related_to',-31)")
    db.execute("insert into business_work_cluster_tasks(rowid,cluster_id,task_id) "
               "values(84,-31,-11)")
    db.execute("insert into business_attention_tasks(rowid,attention_item_id,task_id) "
               "values(85,-41,-11)")
    db.execute("insert into business_attention_proposal_tasks(rowid,attention_item_id,task_id) "
               "values(86,-41,-11)")
    db.execute("insert into business_task_date_evidence(id,task_id,date_type,raw_phrase,"
               "source_signal_id,actor_kind) "
               "values(-43,-11,'next_check_at','tomorrow',-31,'unknown')")
    db.execute("insert into business_task_events(id,task_id,event_type,before_json,"
               "after_json,reason) values(-44,-11,'created','{}','{}','test')")
    db.execute("insert into business_task_dingtalk_links(id,business_task_id,status) "
               "values(-45,-11,'creating')")
    db.execute("update business_task_dingtalk_links set last_error=? where id=-45",
               (b"\x00\xff",))
    db.execute("insert into business_task_follow_ups(id,business_task_id,source_signal_id,"
               "target_conversation_id,target_kind,question_text,scheduled_at,dedupe_key) "
               "values(-46,-11,-31,'conversation','direct','question','now','followup')")
    db.execute("insert into business_task_follow_ups(id,business_task_id,source_signal_id,"
               "target_conversation_id,target_kind,question_text,scheduled_at,dedupe_key) "
               "values(146,-11,-31,'conversation','direct','question','now','high')")
    db.execute("delete from business_task_follow_ups where id=146")
    db.execute("update business_task_follow_ups set revision=? where id=-46", (math.inf,))
    db.execute("insert into business_task_todo_sync_outbox(id,operation_key,"
               "business_task_id,operation) values(-47,'operation',-11,'create')")
    db.execute("insert into business_task_anchor_links(id,task_id,anchor_id,"
               "evidence_signal_id) values(-48,-11,-11,-31)")
    for record_id, column, value, work_column in (
        (-51, "task_id", -11, "work_project_id"),
        (-52, "anchor_id", -11, "work_todo_id"),
        (-53, "project_id", -11, "work_update_id"),
    ):
        db.execute(f"insert into business_legacy_links(id,{column},{work_column}) "
                   "values(?,?,?)", (record_id, value, -31))
    db.commit()


@pytest.mark.parametrize(
    ("kind", "alias"),
    (("task", "task_public_id"), ("project", "project_public_id"),
     ("anchor", "anchor_public_id")),
)
def test_typed_public_parent_key(kind, alias):
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        db.execute("begin")
        create_business_schema(db)
        xinfo = {row[1]: row for row in db.execute("pragma table_xinfo(business)")}
        assert xinfo[alias][6] == 2
        index = db.execute(
            "select sql from sqlite_schema where name=?",
            (f"idx_business_{kind}_public_id",),
        ).fetchone()[0]
        assert index.lower() == (
            f"create unique index idx_business_{kind}_public_id on business({alias})"
        )


def test_source_capture_fresh_is_declarations_only():
    from app.business_schema_release import capture_business_source

    with closing(_source_db("fresh")) as db:
        source = capture_business_source(db)
        assert source["variant"] == "fresh"
        assert len(source["parents"]) == 4
        assert len(source["children"]) == 15
        assert source["fk_findings"] == ()
        assert "rows" not in source


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_exact_19_child_endpoints_and_21_untouched_groups(variant):
    from app.business_schema_release import (
        PUBLIC_ALIAS, RETAINED_CHILD_FKS, SOURCE_PARENTS,
        migrate_business_schema,
    )

    with closing(_source_db(variant)) as db:
        original = {
            name: tuple(tuple(row) for row in db.execute(f"pragma foreign_key_list({name})"))
            for name in RETAINED_CHILD_FKS
        }
        before_columns = {
            name: tuple(tuple(row) for row in db.execute(f"pragma table_xinfo({name})"))
            for name in RETAINED_CHILD_FKS
        }
        db.commit()
        db.execute("begin immediate")
        migrate_business_schema(db)
        translated_count = untouched_count = 0
        for name, endpoints in RETAINED_CHILD_FKS.items():
            actual = tuple(tuple(row) for row in db.execute(f"pragma foreign_key_list({name})"))
            expected = []
            for row in original[name]:
                old = tuple(row)
                if old[3] in endpoints and old[2] == SOURCE_PARENTS[endpoints[old[3]]]:
                    changed = list(old)
                    changed[2] = "business"
                    changed[4] = PUBLIC_ALIAS[endpoints[old[3]]]
                    expected.append(tuple(changed))
                    translated_count += 1
                else:
                    expected.append(old)
                    untouched_count += 1
            assert actual == tuple(expected), name
            assert tuple(tuple(row) for row in db.execute(f"pragma table_xinfo({name})")) == before_columns[name]
        assert (translated_count, untouched_count) == (19, 21)
        db.rollback()


def test_four_kind_deleted_high_waters_inverse_and_reforward():
    from app.business_schema_release import (
        SOURCE_PARENTS, _parent_waters, _recreate_source,
        business_schema_is_current, capture_business_source,
        migrate_business_schema, restore_business_schema,
    )

    source = json.loads(FRESH_DECLARATIONS.read_text())
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=off")
        _recreate_source(db, source)
        db.execute("insert into business_tasks(id,title,stage) values(-11,'kept task','candidate')")
        db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
                   "values(-14,'project','kept','kept anchor')")
        db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
                   "values(-15,-14,'kept project','synthetic')")
        db.execute("insert into business_project_context_revisions("
                   "id,project_id,context_json,evidence_json) values(-16,-15,'{}','[]')")
        db.execute("insert into business_tasks(id,title,stage) values(111,'high','candidate')")
        db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
                   "values(114,'project','high','high')")
        db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
                   "values(115,114,'high','synthetic')")
        db.execute("insert into business_project_context_revisions("
                   "id,project_id,context_json,evidence_json) values(116,115,'{}','[]')")
        for kind, table in reversed(tuple(SOURCE_PARENTS.items())):
            db.execute(f"delete from {table} where id=?", ({
                "task": 111, "anchor": 114, "project": 115,
                "project_context_revision": 116,
            }[kind],))
        source_waters = _parent_waters(db, target=False)
        assert source_waters == {
            "task": 111, "project": 115,
            "anchor": 114, "project_context_revision": 116,
        }
        db.commit()
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        assert business_schema_is_current(db, "fresh")
        assert _parent_waters(db, target=True) == source_waters
        db.commit()
        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        assert not db.execute(
            "select 1 from sqlite_sequence where name like 'business:%'"
        ).fetchone()
        assert _parent_waters(db, target=False) == source_waters
        assert capture_business_source(db)["variant"] == "fresh"
        db.commit()
        db.execute("begin immediate")
        migrate_business_schema(db)
        assert business_schema_is_current(db, "fresh")
        assert _parent_waters(db, target=True) == source_waters
        assert db.execute(
            "select count(*) from business where record_type in "
            "('task','project','anchor','project_context_revision')"
        ).fetchone() == (4,)
        db.commit()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_populated_actual_and_fresh_copy_preserves_all_facts_and_current_inverse(variant):
    from app.business_schema_release import (
        RETAINED_CHILD_FKS, SOURCE_PARENTS, _child_facts, _child_waters,
        _parent_facts, _parent_waters, _projected_parent_facts,
        _source_fk_findings, business_schema_is_current, capture_business_source,
        migrate_business_schema, restore_business_schema,
    )

    with closing(_source_db(variant)) as db:
        _seed_complete_source(db)
        assert capture_business_source(db)["variant"] == variant
        assert _source_fk_findings(db) == ()
        source_parents = _parent_facts(db)
        source_children = _child_facts(db)
        source_waters = (_parent_waters(db, target=False), _child_waters(db))
        assert source_waters[1]["business_task_follow_ups"] == 146
        assert len(source_waters[0]) + len(source_waters[1]) == 13
        source_declarations = capture_business_source(db)
        assert sum(len(item["indexes"]) for item in source_declarations["children"].values()) == 29
        assert sum(len(item["triggers"]) for item in source_declarations["children"].values()) == 3
        assert len(source_parents) == len(SOURCE_PARENTS) == 4
        assert all(source_children[name]["rows"] for name in RETAINED_CHILD_FKS)
        assert ("real",) in tuple(
            (row[source_children["business_task_follow_ups"]["columns"].index("revision")],)
            for row in source_children["business_task_follow_ups"]["types"]
        )
        assert b"\x00\xff" in tuple(
            row[source_parents["task"]["columns"].index("owner_name")]
            for row in source_parents["task"]["rows"]
        )
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        assert business_schema_is_current(db, variant)
        assert _parent_waters(db, target=True) == source_waters[0]
        assert _child_waters(db) == source_waters[1]
        for kind in SOURCE_PARENTS:
            assert _projected_parent_facts(db, kind, variant) == source_parents[kind]
        assert _child_facts(db) == source_children
        assert db.execute("pragma quick_check").fetchone() == ("ok",)
        assert db.execute("pragma foreign_key_check").fetchall() == []
        physical = {
            (kind, public): physical for kind, public, physical in db.execute(
                "select record_type,public_id,id from business"
            )
        }
        assert len(physical) == 7
        assert all(value > 0 for value in physical.values())
        assert physical[("task", -11)] != physical[("project", -11)] != physical[("anchor", -11)]
        assert db.execute("select merged_task_ref from business where record_type='task' "
                          "and public_id=-11").fetchone() == (physical[("task", -12)],)
        assert db.execute("select merged_task_ref from business where record_type='task' "
                          "and public_id=-12").fetchone() == (physical[("task", -11)],)
        db.commit()
        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        assert capture_business_source(db)["variant"] == variant
        assert _parent_facts(db) == source_parents
        assert _child_facts(db) == source_children
        assert _parent_waters(db, target=False) == source_waters[0]
        assert _child_waters(db) == source_waters[1]
        db.commit()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_all_19_translated_endpoints_enforce_native_kind_and_parent_mutation(variant):
    from app.business_schema_release import RETAINED_CHILD_FKS, migrate_business_schema

    wrong_kind_id = {"task": -21, "project": -12, "anchor": -12}
    with closing(_source_db(variant)) as db:
        _seed_complete_source(db)
        db.execute("begin immediate")
        migrate_business_schema(db)
        db.commit()
        db.execute("pragma foreign_keys=on")
        tested = 0
        for table, endpoints in RETAINED_CHILD_FKS.items():
            for column, kind in endpoints.items():
                if table == "business_task_date_evidence":
                    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                        db.execute(
                            "insert into business_task_date_evidence(id,task_id,date_type,"
                            "raw_phrase,source_signal_id,actor_kind) "
                            "values(-143,?,'next_check_at','wrong kind',-31,'unknown')",
                            (wrong_kind_id[kind],),
                        )
                else:
                    rowid = db.execute(
                        f"select rowid from {table} where {column} is not null limit 1"
                    ).fetchone()[0]
                    original = db.execute(
                        f"select {column} from {table} where rowid=?", (rowid,)
                    ).fetchone()[0]
                    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                        db.execute(
                            f"update {table} set {column}=? where rowid=?",
                            (wrong_kind_id[kind], rowid),
                        )
                    assert db.execute(
                        f"select {column} from {table} where rowid=?", (rowid,)
                    ).fetchone() == (original,)
                tested += 1
        assert tested == 19
        for kind in ("task", "project", "anchor"):
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                db.execute("delete from business where record_type=? and public_id=-11", (kind,))
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                db.execute("update business set legacy_key='-99' "
                           "where record_type=? and public_id=-11", (kind,))
        db.execute("update business set legacy_key='-22' "
                   "where record_type='project' and public_id=-21")
        assert db.execute("delete from business where record_type='project' "
                          "and public_id=-22").rowcount == 1
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_inverse_reads_current_target_writes_deletion_and_rebind(variant):
    from app.business_schema_release import (
        business_schema_is_current, capture_business_source,
        migrate_business_schema, restore_business_schema,
    )
    from app.business_storage import insert_business, update_business, delete_business

    with closing(_source_db(variant)) as db:
        _seed_complete_source(db)
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        db.commit()
        db.execute("pragma foreign_keys=on")
        db.execute("begin immediate")
        assert insert_business(db, "anchor", {"id": 123, "anchor_type": "project",
                                               "anchor_ref": "new a", "title": "new a"}).lastrowid == 123
        assert insert_business(db, "anchor", {"id": 124, "anchor_type": "project",
                                               "anchor_ref": "new b", "title": "new b"}).lastrowid == 124
        assert insert_business(db, "project", {"id": 123, "canonical_anchor_id": 123,
                                                "title": "new project", "registry_source": "test"}).lastrowid == 123
        assert insert_business(db, "project_context_revision", {
            "id": 123, "project_id": 123, "context_json": "{}", "evidence_json": "[]",
        }).lastrowid == 123
        assert insert_business(db, "task", {"id": 123, "title": "new task",
                                             "stage": "candidate"}).lastrowid == 123
        assert insert_business(db, "task", {"id": 124, "title": "deleted task",
                                             "stage": "candidate"}).lastrowid == 124
        assert update_business(db, "task", 123, {"title": "edited task"}) == 1
        assert update_business(db, "project", 123, {"canonical_anchor_id": 124}) == 1
        assert delete_business(db, "task", 124) == 1
        db.commit()
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        assert capture_business_source(db)["variant"] == variant
        assert db.execute("select title from business_tasks where id=123").fetchone() == ("edited task",)
        assert db.execute("select 1 from business_tasks where id=124").fetchone() is None
        assert db.execute("select canonical_anchor_id from business_projects "
                          "where id=123").fetchone() == (124,)
        assert db.execute("select project_id from business_project_context_revisions "
                          "where id=123").fetchone() == (123,)
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.commit()
        db.execute("begin immediate")
        migrate_business_schema(db)
        assert business_schema_is_current(db, variant)
        assert db.execute("select title from business where record_type='task' "
                          "and public_id=123").fetchone() == ("edited task",)
        db.rollback()


def test_unrepresentable_corrupted_current_fact_refuses_inverse_and_rollback_retains_target():
    from app.business_schema_release import (
        business_schema_is_current, migrate_business_schema, restore_business_schema,
    )

    with closing(_source_db("fresh")) as db:
        _seed_complete_source(db)
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        db.commit()
        db.execute("pragma ignore_check_constraints=on")
        db.execute("begin immediate")
        assert db.execute("update business set title='' where record_type='task' "
                          "and public_id=-11").rowcount == 1
        db.commit()
        db.execute("pragma ignore_check_constraints=off")
        db.execute("begin immediate")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            restore_business_schema(db, inverse)
        db.rollback()
        assert business_schema_is_current(db, "fresh")
        assert db.execute("select title from business where record_type='task' "
                          "and public_id=-11").fetchone() == ("",)


def test_rollback_after_first_child_drop_restores_exact_source(monkeypatch):
    from app import business_schema_release as release

    with closing(_source_db("fresh")) as db:
        _seed_complete_source(db)
        before = tuple(db.iterdump())

        def fail_after_first_drop(connection):
            connection.execute("drop table main.business_task_evidence")
            raise RuntimeError("first child drop injection")

        monkeypatch.setattr(release, "_drop_source", fail_after_first_drop)
        db.execute("begin immediate")
        with pytest.raises(RuntimeError, match="first child drop injection"):
            release.migrate_business_schema(db)
        db.rollback()
        assert tuple(db.iterdump()) == before
        assert release.capture_business_source(db)["variant"] == "fresh"


def test_empty_sequences_roundtrip_as_absent_not_zero():
    from app.business_schema_release import (
        _child_waters, _parent_waters, _recreate_source, migrate_business_schema,
        restore_business_schema,
    )

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=off")
        _recreate_source(db, _source_declarations("fresh"))
        assert all(value is None for value in _parent_waters(db, target=False).values())
        assert all(value is None for value in _child_waters(db).values())
        db.commit()
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        assert all(value is None for value in _parent_waters(db, target=True).values())
        assert all(value is None for value in _child_waters(db).values())
        db.commit()
        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        assert all(value is None for value in _parent_waters(db, target=False).values())
        assert all(value is None for value in _child_waters(db).values())
        db.rollback()


def test_unrelated_fk_finding_is_identical_across_forward_and_inverse():
    from app.business_schema_release import (
        capture_business_source, migrate_business_schema, restore_business_schema,
    )
    from app.task_schema_release import _foreign_keys

    with closing(_source_db("fresh")) as db:
        db.execute("create table tasks(id integer primary key)")
        db.execute("create table runs(id integer primary key, task_id integer references tasks(id))")
        db.execute("insert into runs(id,task_id) values(1,999)")
        db.commit()
        original = _foreign_keys(db)
        assert len(original) == 1 and original[0][0] == "runs"
        assert capture_business_source(db)["fk_findings"] == original
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        assert _foreign_keys(db) == original
        db.commit()
        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        assert _foreign_keys(db) == original
        db.rollback()


@pytest.mark.parametrize("defect", ("extra_index", "main_view", "temp_view", "mixed_target"))
def test_malformed_source_refused_before_ddl_without_mutation(defect):
    from app.business_schema_release import capture_business_source, migrate_business_schema

    with closing(_source_db("fresh")) as db:
        if defect == "extra_index":
            db.execute("create index unsupported_child_index on business_task_evidence(task_id)")
        elif defect == "main_view":
            db.execute("create view unsupported_business_view as select id from business_tasks")
        elif defect == "temp_view":
            db.execute("create temp view unsupported_business_view as select id from business_tasks")
        else:
            db.execute("create table business(id integer primary key)")
        db.commit()
        before = tuple(db.iterdump())
        with pytest.raises(ValueError):
            capture_business_source(db)
        assert tuple(db.iterdump()) == before
        db.execute("begin immediate")
        with pytest.raises(ValueError):
            migrate_business_schema(db)
        db.rollback()
        assert tuple(db.iterdump()) == before


def test_broken_moved_parent_fk_refused_before_ddl():
    from app.business_schema_release import migrate_business_schema

    with closing(_source_db("fresh")) as db:
        db.execute("insert into business_task_evidence(task_id,signal_id,evidence_role) "
                   "values(999,-31,'discovery')")
        db.commit()
        before = tuple(db.iterdump())
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="broken moved Business parent source"):
            migrate_business_schema(db)
        db.rollback()
        assert tuple(db.iterdump()) == before


def test_current_manifest_rejects_missing_typed_index_without_repair():
    from app.business_schema_release import business_schema_is_current, migrate_business_schema

    with closing(_source_db("fresh")) as db:
        db.execute("begin immediate")
        migrate_business_schema(db)
        assert business_schema_is_current(db, "fresh")
        db.execute("drop index idx_business_task_public_id")
        assert not business_schema_is_current(db, "fresh")
        assert db.execute("select 1 from sqlite_schema where name='idx_business_task_public_id'").fetchone() is None
        db.rollback()


def test_inverse_rejects_tampered_captured_declarations_before_ddl():
    from app.business_schema_release import (
        business_schema_is_current, migrate_business_schema, restore_business_schema,
    )

    with closing(_source_db("fresh")) as db:
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        db.commit()
        before = tuple(db.iterdump())
        inverse["children"]["business_task_evidence"]["sql"] = (
            "create table business_task_evidence(id integer primary key)"
        )
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="unsupported Business source declarations"):
            restore_business_schema(db, inverse)
        assert business_schema_is_current(db, "fresh")
        db.rollback()
        assert tuple(db.iterdump()) == before


def _isolated_parent_source(db):
    for task_id in (-101, -102, -103):
        db.execute("insert into business_tasks(id,title,stage) values(?,?,'candidate')",
                   (task_id, f"task {task_id}"))
    for anchor_id in (-201, -202, -203, -204):
        db.execute("insert into business_anchors(id,anchor_type,anchor_ref,title) "
                   "values(?,'project',?,?)", (anchor_id, f"ref {anchor_id}", f"anchor {anchor_id}"))
    for project_id, anchor_id in ((-301, -202), (-303, -203)):
        db.execute("insert into business_projects(id,canonical_anchor_id,title,registry_source) "
                   "values(?,?,?,'test')", (project_id, anchor_id, f"project {project_id}"))
    db.commit()


def _isolated_child_values(table, column):
    values = {
        "business_task_evidence": dict(task_id=-101, signal_id=-31, evidence_role="discovery"),
        "business_task_date_evidence": dict(task_id=-101, date_type="next_check_at",
                                            raw_phrase="tomorrow", source_signal_id=-31,
                                            actor_kind="unknown"),
        "business_task_events": dict(task_id=-101, event_type="created", before_json="{}",
                                     after_json="{}", reason="test"),
        "business_project_evidence": dict(project_id=-301, signal_id=-31),
        "business_task_dingtalk_links": dict(business_task_id=-101, status="creating"),
        "business_task_follow_ups": dict(business_task_id=-101, source_signal_id=-31,
                                         target_conversation_id="conversation", target_kind="direct",
                                         question_text="question", scheduled_at="now",
                                         dedupe_key="isolated"),
        "business_task_todo_sync_outbox": dict(operation_key="isolated",
                                               business_task_id=-101, operation="create"),
        "business_task_relations": dict(from_task_id=-101, to_task_id=-102,
                                        relation_type="related_to", supporting_signal_id=-31),
        "business_work_cluster_tasks": dict(cluster_id=-31, task_id=-101),
        "business_task_anchor_links": dict(task_id=-101, anchor_id=-201,
                                           evidence_signal_id=-31),
        "business_project_candidates": dict(cluster_id=-31, title="candidate", reason="reason",
                                            status="confirmed", confirmed_project_id=-301,
                                            confirmation_signal_id=-31),
        "business_attention_items": dict(id=-41, stable_key="isolated", category="fyi",
                                         title="attention", why_attention="why",
                                         current_state="state", ceo_action="action",
                                         anchor_id=-201, evidence_signal_id=-31),
        "business_attention_tasks": dict(attention_item_id=-41, task_id=-101),
        "business_attention_proposal_tasks": dict(attention_item_id=-41, task_id=-101),
    }
    if table == "business_legacy_links":
        return {column: {"task_id": -101, "anchor_id": -201, "project_id": -301}[column],
                "work_project_id": -31}
    return values[table]


def _insert_isolated_child(db, table, column):
    if table in ("business_attention_tasks", "business_attention_proposal_tasks"):
        _insert_isolated_child(db, "business_attention_items", "anchor_id")
    values = _isolated_child_values(table, column)
    db.execute(
        f"insert into {table}({','.join(values)}) values({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def _isolated_endpoint_db(variant, table, column):
    from app.business_schema_release import migrate_business_schema

    db = _source_db(variant)
    _isolated_parent_source(db)
    db.execute("begin immediate")
    migrate_business_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    _insert_isolated_child(db, table, column)
    return db


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_each_isolated_endpoint_blocks_its_own_parent_delete_and_public_id_update(variant):
    from app.business_schema_release import RETAINED_CHILD_FKS

    parent_id = {"task": -101, "project": -301, "anchor": -201}
    spare_id = {"task": -103, "project": -303, "anchor": -204}
    checked = 0
    for table, endpoints in RETAINED_CHILD_FKS.items():
        for column, kind in endpoints.items():
            targeted_id = -102 if (table, column) == ("business_task_relations", "to_task_id") else parent_id[kind]
            with closing(_isolated_endpoint_db(variant, table, column)) as db:
                referencing_rows = sum(
                    db.execute(f"select count(*) from {child} where {field}=?",
                               (targeted_id,)).fetchone()[0]
                    for child, fields in RETAINED_CHILD_FKS.items()
                    for field, parent_kind in fields.items() if parent_kind == kind
                )
                assert referencing_rows == 1, (variant, table, column)
                with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                    db.execute("delete from business where record_type=? and public_id=?",
                               (kind, targeted_id))
                with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                    db.execute("update business set legacy_key='-901' "
                               "where record_type=? and public_id=?", (kind, targeted_id))
                assert db.execute("update business set legacy_key='-902' "
                                  "where record_type=? and public_id=?",
                                  (kind, spare_id[kind])).rowcount == 1
                assert db.execute("delete from business where record_type=? and public_id=-902",
                                  (kind,)).rowcount == 1
                assert db.execute("pragma foreign_key_check").fetchall() == []
                db.rollback()
            checked += 1
    assert checked == 19


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_inverse_reads_main_business_when_temp_business_shadows_it(variant):
    from app.business_schema_release import migrate_business_schema, restore_business_schema

    with closing(_source_db(variant)) as db:
        _seed_complete_source(db)
        original = db.execute("select title from main.business_tasks where id=-11").fetchone()
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        db.commit()
        db.execute("create temp table business as select * from main.business")
        db.execute("update temp.business set title='shadow-only title' "
                   "where record_type='task' and public_id=-11")
        assert db.execute("select title from main.business where record_type='task' "
                          "and public_id=-11").fetchone() == original
        db.commit()
        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        assert db.execute("select title from main.business_tasks where id=-11").fetchone() == original
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("shadow", ("business", "business_task_evidence"))
def test_forward_refuses_temp_target_or_child_collision_before_ddl(variant, shadow):
    from app.business_schema_release import capture_business_source, migrate_business_schema

    with closing(_source_db(variant)) as db:
        _seed_complete_source(db)
        db.execute(f"create temp table {shadow}(shadow_marker text)")
        db.commit()
        before = tuple(db.iterdump())
        with pytest.raises(ValueError, match="Business TEMP name collision"):
            capture_business_source(db)
        assert tuple(db.iterdump()) == before
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="Business TEMP name collision"):
            migrate_business_schema(db)
        db.rollback()
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("shadow", ("business_tasks", "business_task_evidence"))
def test_inverse_refuses_temp_source_destination_collision_before_ddl(shadow):
    from app.business_schema_release import business_schema_is_current
    from app.business_schema_release import migrate_business_schema, restore_business_schema

    with closing(_source_db("fresh")) as db:
        _seed_complete_source(db)
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        db.commit()
        db.execute(f"create temp table {shadow}(shadow_marker text)")
        db.commit()
        before = tuple(db.iterdump())
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="Business TEMP name collision"):
            restore_business_schema(db, inverse)
        assert business_schema_is_current(db, "fresh")
        db.rollback()
        assert tuple(db.iterdump()) == before


def test_native_unqualified_index_ddl_binds_temp_relation_first():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table main.business(public_id integer)")
        db.execute("create temp table business(shadow_marker text)")
        with pytest.raises(sqlite3.OperationalError, match="no such column: public_id"):
            db.execute("create index native_binding_probe on business(public_id)")
        assert db.execute("select 1 from main.sqlite_schema "
                          "where name='native_binding_probe'").fetchone() is None
