"""The captured six-table source is the independent migration/inverse oracle."""

import json
from pathlib import Path
import sqlite3

import pytest

from app.run_storage import create_run_schema, insert_run
from app.task_storage import create_task_schema, insert_task


INVENTORY = Path(__file__).parents[1] / (
    "docs/superpowers/specs/2026-10-13-database-redesign-phase5a-inventory.json"
)
SOURCE = json.loads(INVENTORY.read_text())["tables"]
TYPES = tuple(SOURCE)


@pytest.fixture(autouse=True)
def _historical_text_action_dml(monkeypatch):
    """The six-family migration target is historical TEXT, not final compact."""
    from tests.historical_action_text_fixture import install

    install(monkeypatch)


def source_database(path, row_factory=None):
    db = sqlite3.connect(path)
    db.row_factory = row_factory
    create_task_schema(db, include_workbench=True)
    create_run_schema(db, include_workbench=True)
    insert_task(db, "reply_tasks", dict(
        id=1, conversation_id="chat", conversation_title="source title", single_chat=0,
        trigger_message_id="message", trigger_create_time="original", trigger_sender="人",
    ))
    insert_task(db, "work_summary_inputs", dict(
        id=2, legacy_key="1", source_type="test", source_ref="collision", payload_json="{}",
    ))
    for run_id, role in ((1, "consumer"), (2, "consumer"), (3, "audit"), (4, "audit"), (5, "consumer")):
        insert_run(db, "agent_runs", dict(
            id=run_id, task_id=1, execution_generation=f"generation-{run_id}", role=role,
        ))
    insert_run(db, "agent_run_state_events", dict(
        id=6, legacy_key="1", parent_id=1, phase="collision", structured_error_json="",
    ))
    for schema in SOURCE.values():
        db.execute(next(o["sql"] for o in schema["objects"] if o["type"] == "table"))
    for schema in SOURCE.values():
        for obj in schema["objects"]:
            if obj["type"] != "table" and obj["sql"]:
                db.execute(obj["sql"])
    db.executescript("""
        insert into review_candidates values
            (1,1,'代际',1,0,3,7,'raw digest',' { "字": [2, 1] } ','','',' source time '),
            (7,1,'代际',2,1,4,1,'second digest','not JSON, retained source text','','','second time');
        insert into candidate_reviews values
            (1,1,3,'approve','raw digest',' { "outcome": "approve" } ','review time'),
            (7,1,4,'return','second digest','\u4fdd\u7559','other review');
        insert into candidate_selections values (1,7,1,'选项',' { "branch": 1 } ','selected','selection time');
        insert into candidate_supplements values (1,7,'补充\nexact text','supplement time');
        insert into candidate_executions values (1,1,7,1,'uncertain','owner','expiry',' { "x": 2 } ','created','updated');
        insert into candidate_action_attempts values (1,1,0,'effect key','verified',' { "receipt": "原样" } ','created','updated');
        create table external_action_results (
            external_action_key text primary key check(trim(external_action_key) != ''),
            business_object_key text not null check(trim(business_object_key) != ''),
            action_identity text not null check(trim(action_identity) != ''),
            operation text not null check(trim(operation) != ''),
            target_identifiers_json text not null, provider_result_json text not null,
            result_digest text not null check(trim(result_digest) != ''),
            first_agent_run_id integer not null, created_at text not null default current_timestamp,
            foreign key(first_agent_run_id) references runs(id)
        );
        insert into external_action_results values ('independent','business','identity','operation','{}',' { "raw": true } ','digest',1,'exact time');
    """)
    db.commit()
    assert not db.execute("pragma foreign_key_check").fetchall()
    return db


def fingerprint(db):
    return {
        "schema": [tuple(r) for r in db.execute("select type,name,sql from sqlite_master order by type,name")],
        "rows": {
            r[0]: [tuple(row) for row in db.execute(f'select * from "{r[0]}" order by 1')]
            for r in db.execute("select name from sqlite_master where type='table' order by name").fetchall()
        },
    }


def snapshots(db, projections=None):
    return {
        kind: [tuple(r) for r in db.execute(
            f"select * from ({projections[kind]}) order by id" if projections else f"select * from {kind} order by id"
        )] for kind in TYPES
    }


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_exact_forward_inverse_and_forward_preserve_raw_facts_and_crossed_links(tmp_path, row_factory):
    from app.action_storage import ACTION_PROJECTIONS, capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "source.db", row_factory)
    before = fingerprint(db)
    originals = snapshots(db)
    state = capture_action_schema(db)
    assert "raw digest" not in json.dumps(state, ensure_ascii=False)
    db.execute("begin immediate")
    report = migrate_action_schema(db)
    db.commit()
    assert report["counts"] == {k: len(v) for k, v in originals.items()}
    assert snapshots(db, ACTION_PROJECTIONS) == originals
    names = {r[0] for r in db.execute("select name from sqlite_master where type in ('table','view')")}
    assert "actions" in names and not names.intersection(TYPES)
    assert db.execute("select count(distinct id) from actions").fetchone()[0] == 8
    assert not db.execute("pragma foreign_key_check").fetchall()
    row = db.execute("select id,candidate_id,review_id,selection_id from actions where record_type='candidate_executions'").fetchone()
    assert len(set(row)) == 4
    db.execute("begin immediate")
    restore_action_schema(db, state)
    db.commit()
    assert fingerprint(db) == before
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    assert snapshots(db, ACTION_PROJECTIONS) == originals
    db.close()


def test_new_facts_updates_deletes_defaults_and_original_allocator_survive_inverse(tmp_path):
    from app.action_storage import ACTION_PROJECTIONS, capture_action_schema, delete_action, insert_action, migrate_action_schema, restore_action_schema, update_action

    db = source_database(tmp_path / "mutations.db")
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    db.execute("begin immediate")
    first = insert_action(db, "candidate_supplements", dict(candidate_id=1, instruction="追加"))
    assert first.lastrowid == 2 and first.rowcount == 1
    update_action(db, "candidate_supplements", 2, {"instruction": "updated\u0000raw"})
    assert delete_action(db, "candidate_supplements", 2).rowcount == 1
    second = insert_action(db, "candidate_supplements", dict(candidate_id=1, instruction="reused"))
    assert second.lastrowid == 2
    insert_action(db, "candidate_executions", dict(candidate_id=7, review_id=1, status="running"))
    result = db.execute(f"select lease_owner,lease_expires_at,result_json,selection_id,created_at,updated_at from ({ACTION_PROJECTIONS['candidate_executions']}) where id=2").fetchone()
    assert result[:4] == ("", "", "{}", None)
    assert result[4] and result[4] == result[5]
    update_action(db, "candidate_executions", 2, {"status": "done", "result_json": " exact ", "updated_at": "later"})
    assert update_action(db, "candidate_executions", 2, {"status": "failed"}, where_sql="status='running'").rowcount == 0
    db.execute("insert into external_action_results values ('replacement','business','identity2','operation','{}','new receipt','digest2',2,'new time')")
    db.commit()
    projected = snapshots(db, ACTION_PROJECTIONS)
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    restore_action_schema(db, state)
    db.commit()
    assert snapshots(db) == projected
    assert db.execute("select provider_result_json from external_action_results where external_action_key='replacement'").fetchone()[0] == "new receipt"
    assert db.execute("insert into candidate_supplements(candidate_id,instruction) values(1,'next')").lastrowid == 3
    assert not db.execute("pragma foreign_key_check").fetchall()
    db.close()


@pytest.mark.parametrize("mutation", [
    "create index extra_candidate on review_candidates(candidate_digest)",
    "drop trigger trg_runs_child_candidate_reviews_audit_run_id_insert",
    "create trigger extra_candidate_update after update on candidate_supplements begin select 1; end",
    "alter table candidate_supplements add column unapproved text",
    "create table actions(id integer)",
    "create table unknown_child(id integer primary key, candidate_id integer references review_candidates(id))",
    "create index idx_actions_public_id on external_action_results(result_digest)",
])
def test_unknown_source_declarations_stop_before_any_mutation(tmp_path, mutation):
    from app.action_storage import migrate_action_schema

    db = source_database(tmp_path / "unsupported.db")
    db.execute(mutation)
    db.commit()
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="source|declaration|inbound"):
        migrate_action_schema(db)
    assert fingerprint(db) == before
    db.close()


@pytest.mark.parametrize("table,column,value", [
    ("review_candidates", "candidate_json", sqlite3.Binary(b"\x00\xff")),
    ("review_candidates", "stage_index", 2.5),
    ("candidate_action_attempts", "action_index", "not an integer"),
    ("candidate_supplements", "instruction", sqlite3.Binary(b"raw")),
    ("candidate_reviews", "candidate_id", 999),
])
def test_unrepresentable_values_and_dangling_source_links_refuse_before_ddl(tmp_path, table, column, value):
    from app.action_storage import migrate_action_schema

    db = source_database(tmp_path / "values.db")
    db.execute(f"update {table} set {column}=? where id=1", (value,))
    db.commit()
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="source|represent|reference|type"):
        migrate_action_schema(db)
    assert fingerprint(db) == before
    db.close()


@pytest.mark.parametrize("values", [
    {"candidate_id": 1, "review_id": 7, "status": "made-up"},
    {"candidate_id": 7, "review_id": 7, "status": "running", "lease_owner": None},
    {"candidate_id": 7, "review_id": 7, "status": "running", "selection_id": 99},
])
def test_original_notnull_enum_unique_and_fk_constraints(tmp_path, values):
    from app.action_storage import insert_action, migrate_action_schema

    db = source_database(tmp_path / "constraints.db")
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        insert_action(db, "candidate_executions", values)
    with pytest.raises(sqlite3.IntegrityError):
        insert_action(db, "candidate_action_attempts", dict(execution_id=1, action_index=0, external_action_key="different", status="failed"))
    db.close()


def test_finite_payload_and_typed_physical_references_are_enforced_by_sqlite(tmp_path):
    from app.action_storage import action_physical_id, migrate_action_schema

    db = source_database(tmp_path / "typed.db")
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    execution = action_physical_id(db, "candidate_executions", 1)
    candidate = action_physical_id(db, "review_candidates", 1)
    for sql, args in [
        ("update actions set review_id=? where id=?", (candidate, execution)),
        ("update actions set payload_json=json_set(payload_json,'$.invented',1) where id=?", (execution,)),
        ("update actions set payload_json=json_remove(payload_json,'$.status') where id=?", (execution,)),
        ("update actions set payload_json=json_set(payload_json,'$.result_json',json('{}')) where id=?", (execution,)),
        ("update actions set consumer_run_id=6 where id=?", (candidate,)),
        ("update actions set task_id=2 where id=?", (candidate,)),
        ("update actions set candidate_id=? where id=?", (candidate, candidate)),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql, args)
    db.close()


def test_malformed_current_schema_or_inverse_metadata_is_not_modified(tmp_path):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "current.db")
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    bad_state = json.loads(json.dumps(state))
    bad_state["tables"]["candidate_supplements"]["objects"][0]["sql"] += " -- changed"
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="source|metadata|declaration"):
        restore_action_schema(db, bad_state)
    assert fingerprint(db) == before
    db.rollback()
    db.execute("alter table actions add column surprise text")
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="current|actions|schema"):
        restore_action_schema(db, state)
    assert fingerprint(db) == before
    db.close()


def test_projection_point_and_existing_lookup_queries_are_indexed(tmp_path):
    from app.action_storage import ACTION_PROJECTIONS, migrate_action_schema

    db = source_database(tmp_path / "indexes.db")
    db.execute("begin immediate")
    migrate_action_schema(db)
    for kind, predicate in (("review_candidates", "id=1"), ("review_candidates", "consumer_run_id=1"), ("candidate_reviews", "candidate_id=1"), ("candidate_action_attempts", "external_action_key='effect key'")):
        plan = "\n".join(r[3] for r in db.execute(f"explain query plan select * from ({ACTION_PROJECTIONS[kind]}) where {predicate}"))
        assert "SEARCH" in plan and "SCAN a" not in plan, plan
    db.close()


def test_declared_current_objects_repair_without_altering_rows_and_reject_unknowns(tmp_path):
    from app.action_storage import action_schema_is_current, create_action_schema, migrate_action_schema

    db = source_database(tmp_path / "repair.db")
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    before = fingerprint(db)
    assert action_schema_is_current(db)
    create_action_schema(db)
    assert fingerprint(db) == before
    db.execute("drop index idx_actions_external_key")
    db.execute("drop trigger trg_actions_review_id_update")
    assert not action_schema_is_current(db)
    assert action_schema_is_current(db, allow_missing_objects=True)
    create_action_schema(db)
    assert fingerprint(db) == before
    db.execute("create index unknown_action_index on actions(status)")
    unknown = fingerprint(db)
    with pytest.raises(ValueError, match="current|schema|declaration"):
        create_action_schema(db)
    assert fingerprint(db) == unknown
    db.close()


def test_current_payload_extra_facts_cannot_be_silently_lost_on_inverse(tmp_path):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "extra-payload.db")
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("pragma ignore_check_constraints=on")
    db.execute("update actions set payload_json=json_set(payload_json,'$.extra','unrepresentable fact') where record_type='candidate_supplements'")
    db.execute("pragma ignore_check_constraints=off")
    db.commit()
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="payload|current"):
        restore_action_schema(db, state)
    assert fingerprint(db) == before
    db.close()


def test_public_key_update_keeps_original_non_cascading_fk_behavior(tmp_path):
    from app.action_storage import insert_action, migrate_action_schema, update_action

    db = source_database(tmp_path / "public-key.db")
    db.execute("pragma foreign_keys=on")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("update review_candidates set id=100 where id=1")
    db.rollback()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=on")
    with pytest.raises(sqlite3.IntegrityError):
        update_action(db, "review_candidates", 1, {"id": 100})
    assert update_action(db, "candidate_supplements", 1, {"id": 100}).rowcount == 1
    assert insert_action(db, "candidate_supplements", {"candidate_id": 1, "instruction": "next"}).lastrowid == 101
    db.close()


def test_allocator_preserves_negative_ids_upper_bound_and_deleted_highest_on_inverse(tmp_path):
    from app.action_storage import capture_action_schema, delete_action, insert_action, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "allocator.db")
    db.execute("delete from candidate_supplements")
    db.execute("insert into candidate_supplements(id,candidate_id,instruction) values(-7,1,'negative')")
    db.commit()
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    assert insert_action(db, "candidate_supplements", {"candidate_id": 1, "instruction": "next"}).lastrowid == -6
    insert_action(db, "candidate_supplements", {"id": 2**63-1, "candidate_id": 1, "instruction": "maximum"})
    random_id = insert_action(db, "candidate_supplements", {"candidate_id": 1, "instruction": "native allocator"}).lastrowid
    assert 0 < random_id < 2**63-1
    delete_action(db, "candidate_supplements", random_id)
    delete_action(db, "candidate_supplements", 2**63-1)
    delete_action(db, "candidate_supplements", -6)
    restore_action_schema(db, state)
    assert db.execute("insert into candidate_supplements(candidate_id,instruction) values(1,'restored next')").lastrowid == -6
    assert not db.execute("select 1 from sqlite_sequence where name in ('actions','candidate_supplements')").fetchone()
    db.close()


def test_generated_columns_retain_source_sqlite_comparison_affinities(tmp_path):
    from app.action_storage import ACTION_PROJECTIONS, migrate_action_schema

    db = source_database(tmp_path / "affinity.db")
    db.execute("update candidate_supplements set instruction='123' where id=1")
    db.commit()
    assert db.execute("select id from candidate_supplements where instruction=?", (123,)).fetchone()[0] == 1
    assert db.execute("select id from review_candidates where stage_index=?", ("0",)).fetchone()[0] == 1
    db.execute("begin immediate")
    migrate_action_schema(db)
    text_match = db.execute(f"select id from ({ACTION_PROJECTIONS['candidate_supplements']}) where instruction=?", (123,)).fetchone()
    integer_match = db.execute(f"select id from ({ACTION_PROJECTIONS['review_candidates']}) where stage_index=?", ("0",)).fetchone()
    assert text_match == (1,)
    assert integer_match == (1,)
    db.close()


def test_inverse_declaration_name_collision_refuses_before_creating_source_rows(tmp_path):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "inverse-names.db")
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("create index idx_candidate_action_key on external_action_results(result_digest)")
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="declaration|name|source"):
        restore_action_schema(db, state)
    assert fingerprint(db) == before
    db.close()


def test_insert_or_ignore_retains_source_constraint_conflict_behavior(tmp_path):
    from app.action_storage import insert_action, migrate_action_schema

    db = source_database(tmp_path / "ignore.db")
    assert db.execute("insert or ignore into candidate_supplements(candidate_id) values(1)").rowcount == 0
    db.commit()
    db.execute("begin immediate")
    migrate_action_schema(db)
    result = insert_action(db, "candidate_supplements", {"candidate_id": 1}, ignore=True)
    assert result.rowcount == 0 and result.lastrowid is None
    duplicate = insert_action(db, "candidate_action_attempts", dict(execution_id=1, action_index=0, external_action_key="duplicate", status="failed"), ignore=True)
    assert duplicate.rowcount == 0
    db.close()


@pytest.mark.parametrize("declaration", [
    "create view extra_candidate_view as select id from review_candidates",
    'create view "quoted view" as select c."id" from main."review_candidates" c',
    'create view MixedSourceView as select ID from ReViEw_CaNdIdAtEs',
    "create trigger extra_read after insert on external_action_results begin select candidate_json from review_candidates; end",
    "create trigger extra_update after update of provider_result_json on external_action_results begin update candidate_supplements set instruction='changed' where id=1; end",
    "create trigger extra_delete before delete on external_action_results begin delete from candidate_action_attempts where id=1; end",
    "create view extra_bridge as select * from candidate_reviews; create trigger extra_indirect after insert on external_action_results begin select * from extra_bridge; end",
])
def test_external_source_sql_dependencies_refuse_before_mutation(tmp_path, declaration):
    from app.action_storage import migrate_action_schema

    db = source_database(tmp_path / "external-source-dependency.db")
    db.executescript(declaration)
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="dependency"):
        migrate_action_schema(db)
    assert fingerprint(db) == before
    db.close()


@pytest.mark.parametrize("declaration", [
    "create view extra_action_view as select id from actions",
    "create view MixedActionView as select ID from AcTiOnS",
    "create trigger extra_read after insert on external_action_results begin select payload_json from actions; end",
    "create trigger extra_update after update of provider_result_json on external_action_results begin update actions set payload_json=payload_json where id=1; end",
    "create trigger extra_delete before delete on external_action_results begin delete from actions where id=1; end",
    "create view extra_bridge as select first_agent_run_id as id from external_action_results; create trigger extra_instead instead of insert on extra_bridge begin delete from actions where id=new.id; end",
])
def test_external_current_sql_dependencies_refuse_inverse_before_mutation(tmp_path, declaration):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "external-current-dependency.db")
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.executescript(declaration)
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="dependency"):
        restore_action_schema(db, state)
    assert fingerprint(db) == before
    db.close()


def test_unrelated_views_triggers_and_fts_are_preserved_without_running_bodies(tmp_path):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "unrelated-objects.db")
    db.executescript("""
        create virtual table searchable using fts5(body);
        insert into searchable(body) values('exact source text');
        create view unrelated_view as
            select external_action_key as review_candidates,
                   'candidate_reviews actions' as literal from external_action_results;
        create view unrelated_search as select rowid,body from searchable;
        create trigger unrelated_trigger after update of provider_result_json on external_action_results
            begin select raise(abort,'actions review_candidates must not execute'); end;
        create view unrelated_modifiable as select external_action_key from external_action_results;
        create trigger unrelated_instead instead of insert on unrelated_modifiable
            begin select raise(abort,'must not execute'); end;
    """)
    before = fingerprint(db)
    state = capture_action_schema(db)
    observed = []
    db.set_authorizer(lambda *args: observed.append(args) or sqlite3.SQLITE_OK)
    db.execute("begin immediate")
    migrate_action_schema(db)
    restore_action_schema(db, state)
    db.commit()
    assert fingerprint(db) == before
    observed.clear()
    assert db.execute("select * from unrelated_view").fetchone() == ("independent", "candidate_reviews actions")
    assert observed
    db.set_authorizer(None)
    db.close()


@pytest.mark.parametrize("inverse", [False, True])
def test_mixed_case_inbound_fk_is_valid_and_refused_before_either_transition(tmp_path, inverse):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "mixed-inbound.db")
    state = capture_action_schema(db)
    if inverse:
        db.execute("begin immediate")
        migrate_action_schema(db)
        db.commit()
    parent = "AcTiOnS" if inverse else "REVIEW_CANDIDATES"
    db.execute(f"create table MixedChild(id integer primary key, parent_id integer references {parent}(id))")
    db.execute("insert into MixedChild values(1,1)")
    db.commit()
    assert not db.execute("pragma foreign_key_check").fetchall()
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="inbound"):
        restore_action_schema(db, state) if inverse else migrate_action_schema(db)
    assert fingerprint(db) == before
    db.close()


@pytest.mark.parametrize("inverse", [False, True])
def test_mixed_case_reserved_index_collision_refuses_before_either_transition(tmp_path, inverse):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "mixed-reserved-index.db")
    state = capture_action_schema(db)
    if inverse:
        db.execute("begin immediate")
        migrate_action_schema(db)
        db.commit()
    name = "IDX_CANDIDATE_ACTION_KEY" if inverse else "IDX_ACTIONS_PUBLIC_ID"
    db.execute(f"create index {name} on external_action_results(result_digest)")
    before = fingerprint(db)
    db.execute("begin immediate")
    with pytest.raises(ValueError, match="collision"):
        restore_action_schema(db, state) if inverse else migrate_action_schema(db)
    assert fingerprint(db) == before
    db.close()


@pytest.mark.parametrize("name", ["AcTiOnS", "REVIEW_CANDIDATES"])
def test_fresh_creation_refuses_mixed_case_existing_reserved_tables(tmp_path, name):
    from app.action_storage import create_action_schema

    db = sqlite3.connect(tmp_path / "mixed-fresh.db")
    db.execute(f"create table {name}(id integer primary key)")
    before = fingerprint(db)
    with pytest.raises(ValueError, match="source|current|declaration"):
        create_action_schema(db)
    assert fingerprint(db) == before
    db.close()


def test_current_repair_rejects_mixed_case_reserved_index_on_an_unrelated_table(tmp_path):
    from app.action_storage import action_schema_is_current, create_action_schema, migrate_action_schema

    db = source_database(tmp_path / "mixed-repair.db")
    db.execute("begin immediate")
    migrate_action_schema(db)
    db.commit()
    db.execute("drop index idx_actions_audit_run")
    db.execute("drop index idx_actions_external_key")
    db.execute("create index IDX_ACTIONS_EXTERNAL_KEY on external_action_results(result_digest)")
    before = fingerprint(db)
    assert not action_schema_is_current(db, allow_missing_objects=True)
    with pytest.raises(ValueError, match="current|declaration"):
        create_action_schema(db)
    assert fingerprint(db) == before
    db.close()


def test_sqlite_schema_case_folding_preserves_distinct_unicode_and_parser_names(tmp_path):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema, sqlite_schema_name

    assert sqlite_schema_name("ReViEw_CaNdIdAtEs") == "review_candidates"
    assert sqlite_schema_name("ÄßİKΣσς") == "ÄßİKΣσς"
    db = source_database(tmp_path / "unicode-schema.db")
    db.executescript('''
        create table "Ä"(id integer primary key, value text);
        create table "ä"(id integer primary key, value text);
        insert into "Ä" values(1,'upper unicode');
        insert into "ä" values(1,'lower unicode');
        create view "ViewÄ" as select * from "Ä";
        create view "Viewä" as select * from "ä";
        create table _ACTION_DEPENDENCY_CANDIDATE_ACTION_ATTEMPTS(id integer primary key);
        create table _ACTION_DEPENDENCY_ACTIONS(id integer primary key);
    ''')
    before = fingerprint(db)
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    restore_action_schema(db, state)
    db.commit()
    assert fingerprint(db) == before
    assert db.execute('select value from "ViewÄ"').fetchone()[0] == 'upper unicode'
    assert db.execute('select value from "Viewä"').fetchone()[0] == 'lower unicode'
    db.close()


def test_dependency_parser_preserves_distinct_trigger_and_view_namespaces(tmp_path):
    from app.action_storage import capture_action_schema, migrate_action_schema, restore_action_schema

    db = source_database(tmp_path / "object-namespaces.db")
    db.executescript('''
        create view SharedName as select external_action_key from external_action_results;
        create trigger SHAREDNAME after update of provider_result_json on external_action_results
            begin select 'unrelated'; end;
    ''')
    before = fingerprint(db)
    state = capture_action_schema(db)
    db.execute("begin immediate")
    migrate_action_schema(db)
    restore_action_schema(db, state)
    db.commit()
    assert fingerprint(db) == before
    db.close()
