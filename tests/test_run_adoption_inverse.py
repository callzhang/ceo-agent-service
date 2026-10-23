import pytest

from app.run_adoption_inverse import plan_run_adoption_inverse


def _db(*, simple_actions=False):
    import sqlite3
    from app.run_adoption_definitions import TABLES, receipt_schema_objects
    from app.task_run_entity_assembly import _action_objects
    from app.action_entity_storage import create_action

    db = sqlite3.connect(":memory:")
    db.execute("pragma foreign_keys=off")
    db.execute("create table tasks(id integer primary key,kind text)")
    db.execute("create table runs(id integer primary key,task_id integer,role text,execution_generation text,proposal_revision integer,record_type text)")
    db.execute("insert into tasks values(1,'reply')")
    db.executemany("insert into runs values(?,?,?,?,?,?)", [
        (910,1,'consumer','g1',0,'agent_runs'), (911,1,'audit','g1',0,'agent_runs'),
        (920,1,'consumer','g2',1,'agent_runs'), (921,1,'audit','g2',1,'agent_runs'),
        (10,1,'consumer','g1',0,'agent_runs'), (11,1,'audit','g1',0,'agent_runs'),
        (20,1,'consumer','g2',1,'agent_runs'), (21,1,'audit','g2',1,'agent_runs'),
    ])
    from app.run_entity_definitions import TABLES as RUN_TABLES
    db.execute(RUN_TABLES["run_provenance"])
    db.execute(RUN_TABLES["run_projections"])
    for name, sql in _action_objects().items():
        if simple_actions:
            if name == "actions":
                db.execute("create table actions(id integer primary key)")
            elif sql.lstrip().lower().startswith("create table"):
                db.execute(sql)
        else:
            db.execute(sql)
    db.execute("create table _seq_init(id integer primary key autoincrement)")
    db.execute("drop table _seq_init")
    for sql in TABLES.values():
        db.execute(sql)
    for sql in receipt_schema_objects().values():
        db.execute(sql)
    from app.action_entity_definitions import ACTION_IDENTITY_MAP_DDL
    for sql in ACTION_IDENTITY_MAP_DDL:
        db.execute(sql)
    if simple_actions:
        db.execute("insert into actions(id) values(1)")
    else:
        create_action(db, values={"business_object_key": "b", "action_identity": "a",
                                  "operation": "send", "parameters_json": "{}"})
    db.execute("insert into sqlite_sequence(name,seq) values('actions',340)")
    db.commit()
    db.execute("begin")
    db.execute("insert into run_adopted_results values(10,0,null,'d','{}','','','created')")
    db.execute("insert into run_adopted_results values(20,2,null,'d2','{}','','','created2')")
    db.execute("insert into run_adopted_reviews values(7,11,10,'d','approve','{}','review')")
    db.execute("insert into run_adopted_reviews values(8,21,20,'d2','approve','{}','review2')")
    db.execute("insert into run_adopted_selections values(18,10,11,'x','selected','s')")
    db.execute("insert into run_adopted_supplements values(30,10,'one','a')")
    db.execute("insert into run_adopted_supplements values(31,10,'two','b')")
    db.execute("insert into run_execution_progress values(10,11,18,'done','','','{}','a','b')")
    db.execute("insert into run_execution_progress values(20,21,null,'done','','','{}','c','d')")
    db.execute("insert into run_adoption_identity_map values('review_candidates',100,'40','integer',10,null)")
    db.execute("insert into run_adoption_identity_map values('candidate_reviews',101,'7','integer',11,7)")
    db.execute("insert into run_adoption_identity_map values('candidate_selections',102,'18','integer',10,18)")
    db.execute("insert into run_adoption_identity_map values('candidate_supplements',103,'30','integer',10,30)")
    db.execute("insert into run_adoption_identity_map values('candidate_execution_alias',104,'90','integer',10,null)")
    return db


def _metadata(db):
    catalog = [list(r) for r in db.execute("select type,name,sql from sqlite_schema where tbl_name='actions' and type in ('table','index','trigger') and name not like 'sqlite_%' order by type,name")]
    return ({"version": 1, "action_catalog": catalog, "receipt_catalog": [], "observer_catalog": [],
             "identity_catalog": [], "receipt_columns": [], "source_sequences": [[1,"actions",340]],
             "selected_counts": [], "source_fk_findings": []},
            {"version": 1, "identities": [
                ["review_candidates",100,10,None], ["candidate_reviews",101,11,7],
                ["candidate_selections",102,10,18], ["candidate_supplements",103,10,30],
                ["candidate_execution_alias",104,10,None]],
             "source_sequences": [[1,"actions",340]]})


def test_real_native_owner_facts_plan_retained_and_new_identities_with_run_handoff():
    db = _db()
    try:
        source, forward = _metadata(db)
        from app.run_entity_schema import run_handoff_context, run_handoff_digest
        edges = [
                {"source_kind": "agent_runs", "canonical_id": 10, "source_physical_id": 910},
                {"source_kind": "agent_runs", "canonical_id": 11, "source_physical_id": 911},
                {"source_kind": "agent_runs", "canonical_id": 20, "source_physical_id": 920},
                {"source_kind": "agent_runs", "canonical_id": 21, "source_physical_id": 921}]
        source_context = run_handoff_context({"source_objects": []})
        owner_highwaters = {}
        handoff = {"version": 3, "source_context": source_context,
            "identity_edges": [
                *edges], "runtime_attempt_parent_edges": [], "runtime_attempt_orphan_workload_edges": [], "owner_highwaters": owner_highwaters}
        handoff["digest"] = run_handoff_digest(source_context, edges, [], [], owner_highwaters)
        run_inverse = {"source_objects": [], "sequence_rows": [],
                       "run_fk_findings": [], "run_handoff": handoff}
        plan = plan_run_adoption_inverse(db, source, forward, run_inverse_metadata=run_inverse)
        assert plan["source_run_physical_by_canonical"] == {10: 910, 11: 911, 20: 920, 21: 921}
        existing_review = next(x for x in plan["adoptions"] if x["source_kind"] == "candidate_reviews" and x["native_owner"] == [8, 21])
        assert existing_review["new"] is True
        assert existing_review["physical_id"] > max(plan["allocator_reserved_ids"])
        assert existing_review["public_id"] == "8" and existing_review["public_id_type"] == "text"
        assert plan["source_sequences"] == [[1, "actions", 340]]
        assert plan["current_sequences"] == [[1, "actions", 340]]
        assert plan["planned_sequences"] == [[1, "actions", 340]]
        assert all("body" not in key and "candidate_json" not in key for key in plan)
        assert db.execute("select seq from sqlite_sequence where name='actions'").fetchone() == (340,)
        ddl = db.execute("select sql from sqlite_schema where name='actions'").fetchone()[0]
        assert "AUTOINCREMENT" not in ddl.upper()
    finally:
        db.close()


def test_planner_rejects_missing_positive_run_handoff_owner():
    db = _db()
    try:
        source, forward = _metadata(db)
        from app.run_entity_schema import run_handoff_context, run_handoff_digest
        context = run_handoff_context({"source_objects": []})
        inverse = {"source_objects": [], "sequence_rows": [], "run_fk_findings": [],
                   "run_handoff": {"version": 3, "source_context": context,
                       "identity_edges": [], "runtime_attempt_parent_edges": [], "runtime_attempt_orphan_workload_edges": [], "owner_highwaters": {}, "digest": ""}}
        inverse["run_handoff"]["digest"] = run_handoff_digest(context, [], [], [], {})
        db.execute("delete from runs where id in (10,11,20,21,910,911,920,921)")
        with pytest.raises(ValueError, match="missing source Run owner"):
            plan_run_adoption_inverse(db, source, forward,
                                     run_inverse_metadata=inverse)
    finally:
        db.close()


def test_inverse_weak_run_reference_uses_sparse_edge_or_actual_source_membership():
    from app.run_adoption_inverse import _inverse_weak_run_ref, _run_sources

    db = _db()
    try:
        db.execute("insert into runs values(30,1,'consumer','g3',2,'agent_runs')")
        run_map, source_runs = _run_sources(db, _inverse_for_fixture())
        assert _inverse_weak_run_ref(10, run_map, source_runs) == 910
        assert _inverse_weak_run_ref(30, run_map, source_runs) == 30
        assert _inverse_weak_run_ref(0, run_map, source_runs) == 0
        assert _inverse_weak_run_ref(None, run_map, source_runs) is None
        with pytest.raises(ValueError, match="missing source Run owner: 31"):
            _inverse_weak_run_ref(31, run_map, source_runs)
    finally:
        db.close()


def test_adoption_inverse_rejects_orphan_email_source_key_collision():
    import hashlib
    from app.run_entity_schema import run_handoff_digest
    from app.run_adoption_inverse import _run_sources

    db = _db()
    try:
        source_key = "email-classification:" + hashlib.sha256(b"collision").hexdigest()
        db.execute("alter table runs add column workload_kind text")
        db.execute("alter table runs add column workload_key text")
        db.execute("alter table runs add column parent_id integer")
        db.execute("insert into runs(id,record_type,workload_kind,workload_key,parent_id) "
                   "values(50,'agent_runtime_attempts','email_classification',?,null)",
                   (source_key,))
        db.execute("create table task_run_identity_map(source_kind text,physical_id integer,"
                   "public_key text,public_key_type text,target_id integer)")
        db.execute("insert into task_run_identity_map values(?,?,?,?,?)",
                   ("email_agent_classification_tasks", 55, source_key, "text", 700))
        run_inverse = _inverse_for_fixture()
        orphan = {"attempt_source_physical_id": 50,
                  "workload_kind": "email_classification",
                  "source_operation_key": source_key, "native_workload_key": "999"}
        handoff = run_inverse["run_handoff"]
        handoff["runtime_attempt_orphan_workload_edges"] = [orphan]
        handoff["digest"] = run_handoff_digest(
            handoff["source_context"], handoff["identity_edges"],
            handoff["runtime_attempt_parent_edges"],
            handoff["runtime_attempt_orphan_workload_edges"], handoff["owner_highwaters"])
        before = tuple(db.execute("select * from runs order by id"))
        with pytest.raises(ValueError, match="source key collides"):
            _run_sources(db, run_inverse)
        assert tuple(db.execute("select * from runs order by id")) == before
    finally:
        db.close()


def test_reused_review_business_id_under_another_audit_does_not_reuse_stale_source_identity():
    db = _db()
    try:
        source, forward = _metadata(db)
        db.execute("delete from run_adopted_selections where id=18")
        db.execute("delete from run_execution_progress where consumer_run_id=10")
        db.execute("delete from run_adopted_reviews where id in (7,8)")
        db.execute("insert into run_adopted_reviews values(7,21,20,'d2','approve','{}','reused')")
        assert db.execute("select audit_run_id from run_adopted_reviews where id=7").fetchone() == (21,)
        run_inverse = _inverse_for_fixture()
        plan = plan_run_adoption_inverse(db, source, forward,
                                         run_inverse_metadata=run_inverse)
        reused = next(item for item in plan["adoptions"]
                      if item["source_kind"] == "candidate_reviews"
                      and item["native_owner"] == [7, 21])
        assert reused["new"] is True
        assert reused["mapped_physical_id"] is None
        assert reused["physical_id"] != 101
        assert (reused["public_id"], reused["public_id_type"]) == ("7", "text")
        assert plan["retired_identity_count"] >= 1
    finally:
        db.close()


def test_new_alias_public_id_uses_cast_integer_namespace_with_text_storage():
    db = _db()
    try:
        source, forward = _metadata(db)
        db.execute("update run_adoption_identity_map set source_public_id='1',"
                   "source_public_id_type='text' where source_kind='candidate_execution_alias'")
        plan = plan_run_adoption_inverse(db, source, forward,
                                         run_inverse_metadata=_inverse_for_fixture())
        existing = next(item for item in plan["adoptions"]
                        if item["source_kind"] == "candidate_execution_alias"
                        and item["canonical_run_id"] == 10)
        new_alias = next(item for item in plan["adoptions"]
                         if item["source_kind"] == "candidate_execution_alias"
                         and item["canonical_run_id"] == 20)
        assert (existing["public_id"], existing["public_id_type"]) == ("1", "text")
        assert (new_alias["public_id"], new_alias["public_id_type"]) == ("2", "text")
    finally:
        db.close()


def test_retained_action_collision_reassigns_only_adoption_physical_identity():
    db = _db(simple_actions=True)
    try:
        source, forward = _metadata(db)
        db.execute("update actions set id=101 where id=1")
        before = tuple(db.execute("select * from actions where id=101").fetchone())
        plan = plan_run_adoption_inverse(db, source, forward,
                                         run_inverse_metadata=_inverse_for_fixture())
        review = next(item for item in plan["adoptions"]
                      if item["source_kind"] == "candidate_reviews"
                      and item["native_owner"] == [7, 11])
        assert review["new"] is False and review["reassigned"] is True
        assert review["mapped_physical_id"] == 101
        assert review["physical_id"] > max(plan["allocator_reserved_ids"])
        assert (review["public_id"], review["public_id_type"]) == (7, "integer")
        assert tuple(db.execute("select * from actions where id=101").fetchone()) == before
        assert db.execute("select count(*) from sqlite_sequence where name='actions'").fetchone()[0] == 1
    finally:
        db.close()


def test_cross_kind_duplicate_source_physical_identity_is_rejected_without_mutation():
    db = _db()
    try:
        source, forward = _metadata(db)
        db.execute("update run_adoption_identity_map set source_public_id='7',"
                   "source_public_id_type='integer' where source_kind='candidate_selections'")
        valid = plan_run_adoption_inverse(db, source, forward,
                                          run_inverse_metadata=_inverse_for_fixture())
        same_public_id = [item for item in valid["adoptions"]
                          if item["source_kind"] in {"candidate_reviews", "candidate_selections"}
                          and item["public_id"] == 7]
        assert {item["source_kind"] for item in same_public_id} == {
            "candidate_reviews", "candidate_selections"}
        db.execute("update run_adoption_identity_map set source_physical_id=101 "
                   "where source_kind='candidate_selections'")
        forward["identities"] = [
            [kind, 101 if kind == "candidate_selections" else physical, run_id, child]
            for kind, physical, run_id, child in forward["identities"]
        ]
        # Public integer 7 is valid in both per-kind namespaces; only the
        # shared physical Action ID is malformed here.
        before_map = [tuple(row) for row in db.execute(
            "select * from run_adoption_identity_map order by source_kind,source_physical_id")]
        before_actions = [tuple(row) for row in db.execute("select * from actions order by id")]
        with pytest.raises(ValueError, match="duplicate adoption physical identity"):
            plan_run_adoption_inverse(db, source, forward,
                                      run_inverse_metadata=_inverse_for_fixture())
        assert [tuple(row) for row in db.execute(
            "select * from run_adoption_identity_map order by source_kind,source_physical_id")] == before_map
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before_actions
    finally:
        db.close()


def _inverse_for_fixture():
    from app.run_entity_schema import run_handoff_context, run_handoff_digest

    edges = [
        {"source_kind": "agent_runs", "canonical_id": 10, "source_physical_id": 910},
        {"source_kind": "agent_runs", "canonical_id": 11, "source_physical_id": 911},
        {"source_kind": "agent_runs", "canonical_id": 20, "source_physical_id": 920},
        {"source_kind": "agent_runs", "canonical_id": 21, "source_physical_id": 921},
    ]
    context = run_handoff_context({"source_objects": []})
    highwaters = {}
    handoff = {"version": 3, "source_context": context, "identity_edges": edges,
               "runtime_attempt_parent_edges": [], "runtime_attempt_orphan_workload_edges": [],
               "owner_highwaters": highwaters}
    handoff["digest"] = run_handoff_digest(context, edges, [], [], highwaters)
    return {"source_objects": [], "sequence_rows": [], "run_fk_findings": [],
            "run_handoff": handoff}


def test_actual_c2a_task_run_inverse_planner_uses_current_review_and_source_run_handoff():
    from app.run_adoption_schema import migrate_run_adoptions
    from app.run_entity_schema import restore_run_entities
    from app.task_entity_schema import restore_task_entities
    from tests.test_run_adoption_schema import _populated_source

    db, context = _populated_source(remap_tasks=True, remap_runs=True,
                                    nonagent_run_owner=True,
                                    stop_before_adoption=True)
    try:
        db.row_factory = __import__("sqlite3").Row
        adoption_forward = migrate_run_adoptions(
            db, context["adoption_source"], task_metadata=context["task_metadata"],
            run_metadata=context["run_metadata"])
        task_inverse = restore_task_entities(db, context["task_source_metadata"],
                                             context["task_metadata"])
        run_inverse = restore_run_entities(
            db, context["run_source_metadata"], context["run_metadata"],
            task_metadata=context["task_metadata"], task_inverse_metadata=task_inverse)
        source = context["adoption_source"]
        before_actions = [tuple(row) for row in db.execute("select * from actions order by id")]
        before_reviews = [tuple(row) for row in db.execute(
            "select * from run_adopted_reviews order by id")]
        before_identity_map = [tuple(row) for row in db.execute(
            "select * from run_adoption_identity_map order by source_kind,source_physical_id")]
        before_sequences = [tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")]
        plan = plan_run_adoption_inverse(db, source, adoption_forward,
                                         run_inverse_metadata=run_inverse)
        repeated_plan = plan_run_adoption_inverse(
            db, source, adoption_forward, run_inverse_metadata=run_inverse)
        assert repeated_plan == plan
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before_actions
        assert [tuple(row) for row in db.execute(
            "select * from run_adopted_reviews order by id")] == before_reviews
        assert [tuple(row) for row in db.execute(
            "select * from run_adoption_identity_map order by source_kind,source_physical_id")] == before_identity_map
        assert [tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")] == before_sequences
        old_review = next(item for item in plan["adoptions"]
                          if item["source_kind"] == "candidate_reviews"
                          and item["public_id"] == "7")
        assert old_review["public_id_type"] == "text"
        assert old_review["source_run_id"] == 9007
        assert plan["source_sequences"] == source["source_sequences"]
        assert not any(row[1] == "actions" for row in plan["source_sequences"])
        assert not any(row[1] == "actions" for row in plan["current_sequences"])
        assert plan["source_run_physical_by_canonical"] == {
            9106: 9006, 9107: 9007, 9110: 9010, 9111: 9011,
        }
        assert {edge["source_kind"] for edge in run_inverse["run_handoff"]["identity_edges"]} == {
            "agent_runs", "agent_runtime_attempts",
        }
        assert plan["planned_sequences"] == source["source_sequences"]
        assert plan["current_sequences"] == []
        occupied = {row[0] for row in db.execute("select id from actions")}
        assert occupied.isdisjoint(item["physical_id"] for item in plan["adoptions"]
                                   if item["new"] or item["reassigned"])
    finally:
        db.close()


def test_actual_task_run_then_adoption_inverse_restores_source_action_catalog_and_facts():
    import json
    import sqlite3

    from app.run_adoption_schema import capture_run_adoptions
    from app.run_adoption_inverse import restore_run_adoptions
    from app.run_adoption_schema import migrate_run_adoptions
    from app.run_entity_schema import capture_run_entities, migrate_run_entities, restore_run_entities
    from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
    from tests.test_run_adoption_schema import _populated_source, _source_snapshot

    db, context = _populated_source(remap_tasks=True, remap_runs=True,
                                    nonagent_run_owner=True,
                                    stop_before_adoption=True)
    db.row_factory = sqlite3.Row
    original = context["source_snapshot"]
    try:
        forward = migrate_run_adoptions(
            db, context["adoption_source"], task_metadata=context["task_metadata"],
            run_metadata=context["run_metadata"])
        task_inverse = restore_task_entities(
            db, context["task_source_metadata"], context["task_metadata"])
        run_inverse = restore_run_entities(
            db, context["run_source_metadata"], context["run_metadata"],
            task_metadata=context["task_metadata"], task_inverse_metadata=task_inverse)
        result = restore_run_adoptions(
            db, context["adoption_source"], forward,
            task_metadata=context["task_metadata"],
            task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert result == {"version": 1, "action_count": 10, "receipt_count": 2,
                          "observer_count": 2, "retained_count": 1,
                          "adopted_count": 9, "reassigned_identity_count": 0}
        db.row_factory = None
        restored = _source_snapshot(db)
        assert restored["catalog"] == original["catalog"]
        assert restored["rows"][2:] == original["rows"][2:]
        action_sequence_names = {"actions", "action_receipts", "action_observers",
                                 "action_entity_identity_map"}
        assert tuple(row for row in restored["sequences"] if row[1] in action_sequence_names) == tuple(
            row for row in original["sequences"] if row[1] in action_sequence_names)
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select count(*) from temp.sqlite_schema where name like '_run_adoption_%'").fetchone()[0] == 0

        # Re-capture the restored source and perform a fresh portable forward.
        # This proves Selection 18 survives the complete physical -> native ->
        # source -> native path, rather than only the inverse row builder.
        source_receipts = tuple(db.execute(
            "select id,provider_result_json,result_digest,status,created_at "
            "from action_receipts order by id"))
        source_run_provenance = tuple(db.execute(
            "select id,projection_json,source_type,job_id from runs "
            "where record_type='agent_runs' order by id"))
        task_source = capture_task_entities(db)
        run_source = capture_run_entities(db)
        adoption_source = capture_run_adoptions(db)
        task_forward = migrate_task_entities(
            db, task_source, source_context=task_inverse["source_context"],
            inverse_identity=json.loads(json.dumps(task_inverse["inverse_identity"])))
        run_forward = migrate_run_entities(
            db, run_source, task_metadata=task_forward,
            run_handoff=json.loads(json.dumps(run_inverse["run_handoff"])))
        adoption_forward = migrate_run_adoptions(
            db, adoption_source, task_metadata=task_forward, run_metadata=run_forward)

        audit_target = next(edge["target_id"] for edge in run_forward["identity_edges"]
                           if edge["source_kind"] == "agent_runs" and edge["physical_id"] == 9007)
        consumer_target = next(edge["target_id"] for edge in run_forward["identity_edges"]
                              if edge["source_kind"] == "agent_runs" and edge["physical_id"] == 9006)
        assert db.execute("select consumer_run_id,selection_id,status from run_execution_progress").fetchone() == (
            consumer_target, 18, "done")
        assert db.execute("select id,consumer_run_id,audit_run_id,decision,result_json "
                          "from run_adopted_reviews where id=7").fetchone() == (
            7, consumer_target, audit_target, "approve", '{"outcome":"approve"}')
        assert db.execute("select id,consumer_run_id,audit_run_id,option_key "
                          "from run_adopted_selections where id=18").fetchone() == (
            18, consumer_target, audit_target, "stop")
        assert db.execute("select candidate_digest,candidate_json from run_adopted_results "
                          "where consumer_run_id=?", (consumer_target,)).fetchone() == (
            context["digest"], context["candidate_json"])
        assert tuple(db.execute(
            "select id,provider_result_json,result_digest,status,created_at "
            "from action_receipts order by id")) == source_receipts
        run_targets = {edge["physical_id"]: edge["target_id"]
                       for edge in run_forward["identity_edges"]
                       if edge["source_kind"] == "agent_runs"}
        for source_id, projection, source_type, job_id in source_run_provenance:
            target_id = run_targets[source_id]
            projected = db.execute("select projection_json from run_projections where run_id=?",
                                    (target_id,)).fetchone()
            assert (None if projected is None else projected[0]) == projection
            provenance = db.execute("select source_type,job_id from run_provenance where run_id=?",
                                    (target_id,)).fetchone()
            assert provenance == ((source_type, job_id) if source_type is not None else None)
        assert adoption_forward["receipt_count"] == len(source_receipts)
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()
