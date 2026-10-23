import sqlite3

import pytest

from app.run_adoption_inverse import retained_action_source_rows
from app.storage_payload import ACTION_RECORD_TYPES


def _inverse_fixture(*, feedback=False, generic_new=False, task_delivery=False,
                     unresolved_task=False, before_inverse=None, remap_tasks=True):
    from app.run_adoption_schema import migrate_run_adoptions
    from app.run_entity_schema import restore_run_entities
    from app.task_entity_schema import restore_task_entities
    from tests.test_run_adoption_schema import _populated_source

    db, context = _populated_source(remap_tasks=remap_tasks, remap_runs=True,
                                    feedback=feedback, stop_before_adoption=True)
    db.row_factory = sqlite3.Row
    forward = migrate_run_adoptions(db, context["adoption_source"],
        task_metadata=context["task_metadata"], run_metadata=context["run_metadata"])
    if before_inverse is not None:
        before_inverse(db, context, forward)
    if generic_new:
        from app.action_entity_storage import create_action
        for key, run_id in (("new-native-action", 9106), ("new-zero-run-action", 0)):
            create_action(db, values={"business_object_key": "new-business",
                "action_identity": key, "operation": "send",
                "parameters_json": "{}", "agent_run_id": run_id})
    if task_delivery:
        from app.action_storage import insert_task_delivery_action
        insert_task_delivery_action(db, "task_memory_write_events", {
            "reply_task_id": 1201, "execution_generation": "initial",
        })
        if unresolved_task:
            db.execute("update actions set td_reply_task_id=7777 where td_reply_task_id=1201")
    task_inverse = restore_task_entities(db, context["task_source_metadata"],
                                         context["task_metadata"])
    run_inverse = restore_run_entities(db, context["run_source_metadata"],
        context["run_metadata"], task_metadata=context["task_metadata"],
        task_inverse_metadata=task_inverse)
    return db, context, forward, task_inverse, run_inverse


def test_adopted_feedback_transition_workbench_refs_roundtrip_with_typed_task_handoff():
    import json

    from app.run_adoption_schema import (
        capture_run_adoptions, migrate_run_adoptions,
    )
    from app.run_adoption_inverse import restore_run_adoptions
    from app.run_entity_schema import (
        capture_run_entities, migrate_run_entities, restore_run_entities,
    )
    from app.task_entity_schema import (
        capture_task_entities, migrate_task_entities, restore_task_entities,
    )
    from app.task12_storage import task12_projection
    from tests.test_run_adoption_schema import _populated_source

    db, context = _populated_source(feedback=True, feedback_transition=True,
                                    remap_workbench=True, remap_runs=True,
                                    remap_feedback_rounds=True,
                                    stop_before_adoption=True)
    try:
        from app.action_feedback_storage import insert_feedback_action

        def forward(task_source, run_source, adoption_source, task_handoff=None,
                    run_handoff=None):
            task_forward = migrate_task_entities(db, task_source,
                source_context=task_handoff.get("source_context") if task_handoff else None,
                inverse_identity=(json.loads(json.dumps(task_handoff["inverse_identity"]))
                                  if task_handoff else None))
            run_forward = migrate_run_entities(db, run_source, task_metadata=task_forward,
                run_handoff=(json.loads(json.dumps(run_handoff["run_handoff"]))
                             if run_handoff else None))
            adoption_forward = migrate_run_adoptions(db, adoption_source,
                task_metadata=task_forward, run_metadata=run_forward)
            return task_forward, run_forward, adoption_forward

        def inverse(task_source, run_source, adoption_source, task_forward,
                    run_forward, adoption_forward):
            task_inverse = restore_task_entities(db, task_source, task_forward)
            run_inverse = restore_run_entities(db, run_source, run_forward,
                task_metadata=task_forward, task_inverse_metadata=task_inverse)
            restore_run_adoptions(db, adoption_source, adoption_forward,
                task_metadata=task_forward, task_inverse_metadata=task_inverse,
                run_inverse_metadata=run_inverse)
            return task_inverse, run_inverse

        def transition_rows():
            return tuple(db.execute(
                "select legacy_key,json_extract(payload_json,'$[1]'),"
                "json_extract(payload_json,'$[6]'),json_type(payload_json,'$[6]'),"
                "json_extract(payload_json,'$[7]') from actions "
                "where record_type='feedback_processing_transitions' "
                "order by cast(legacy_key as integer)"))

        def decision_member_rows():
            return tuple(db.execute(
                "select legacy_key,fb_decision_id,fb_feedback_key,fb_round_id "
                "from actions where record_type='feedback_iteration_decision_items' "
                "order by cast(legacy_key as integer)"))

        task_source = context["task_source_metadata"]
        run_source = context["run_source_metadata"]
        adoption_source = context["adoption_source"]
        task_forward = context["task_metadata"]
        run_forward = context["run_metadata"]
        adoption_forward = migrate_run_adoptions(db, adoption_source,
            task_metadata=task_forward, run_metadata=run_forward)
        assert transition_rows() == (
            ("8", 1229, "1202", "text", "1203"),
            ("9", 1229, "1202", "text", "unknown-turn"),
            ("10", 1229, "", "text", ""))
        assert decision_member_rows() == (
            ("5", 416922, "feedback-a", 1229),
            ("6", 227, "feedback-a", 1229))

        # These explicit writes exercise current native target owners created
        # after the initial Task→Run→adoption migration. Feedback runtime writers
        # have not been converted yet, so use the real target tables and existing
        # native Task/Action storage contracts directly.
        db.execute("insert into workbench_tasks(id,title,runtime_kind) values(?,?,?)",
                   (16002, "Native feedback task", "codex"))
        task_columns = ("id,kind,status,available_at,attempts,error,locked_at,lease_owner,"
                        "lease_expires_at,owner_pid,generation,adapter_name,execution_generation,"
                        "current_input_id,claimed_input_id,input_version,claimed_input_version,"
                        "parameters_json,created_at,updated_at")
        db.execute(f"insert into tasks({task_columns}) values(?, 'workbench_turn', 'completed', "
                   "NULL, NULL, NULL, NULL, '', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                   "NULL, jsonb('{}'), 'native', 'native')", (500002,))
        db.execute("insert into workbench_turns(task_id,workbench_task_id,client_request_id,"
                   "task_sequence,user_text,stop_requested,error_code,error_detail,resume_context,"
                   "execution_run_id,runtime_quiesced_run_id,started_at,completed_at) "
                   "values(?, ?, ?, 1, ?, 0, '', '', '', '', '', '', '')",
                   (500002, 16002, "00000000-0000-4000-8000-000000000001", "native feedback turn"))
        db.execute(f"insert into tasks({task_columns}) values(?, 'feedback_processing', 'processing', "
                   "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                   "NULL, jsonb('{}'), 'native', 'native')", (500001,))
        db.execute("insert into feedback_processing_items(task_id,feedback_key,batch_id,"
                   "workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,commit_sha,"
                   "test_evidence_json,restart_evidence_json,health_evidence_json,note,resolved_at,"
                   "current_round_id) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (500001, "feedback-native-new", "batch-a", "16002", "500002", 44,
                    13003, "", "{}", "{}", "{}", "native current item", "", 15001))
        db.execute("insert into feedback_processing_rounds(id,feedback_key,round_number,batch_id,"
                   "status,workbench_task_id,workbench_turn_id,attempt_id,agent_run_id) "
                   "values(?, ?, 1, 'batch-a', 'processing', ?, ?, 44, 13003)",
                   (15001, "feedback-native-new", "16002", "500002"))
        db.execute("insert into feedback_iteration_decisions(id,batch_id,workbench_task_id,"
                   "workbench_turn_id,decision_json) values(?, 'batch-a', ?, ?, '{}')",
                   (15002, "16002", "500002"))
        insert_feedback_action(db, "feedback_iteration_decision_items", {
            "decision_id": 15002, "feedback_key": "feedback-native-new", "round_id": 15001,
        }, source_rowid=15003)
        insert_feedback_action(db, "feedback_processing_transitions", {
            "feedback_key": "feedback-native-new", "round_id": 15001, "batch_id": "batch-a",
            "from_status": "pending", "to_status": "processing", "reason": "native current graph",
            "workbench_task_id": "16002", "workbench_turn_id": "500002",
            "created_at": "native current graph",
        }, source_rowid=15004)
        assert tuple(db.execute(
            "select task_id,feedback_key,workbench_task_id,workbench_turn_id,attempt_id,"
            "agent_run_id,current_round_id from feedback_processing_items "
            "where feedback_key='feedback-native-new'")) == (
            (500001, "feedback-native-new", "16002", "500002", 44, 13003, 15001),)
        assert tuple(db.execute(
            "select id,workbench_task_id,workbench_turn_id from feedback_processing_rounds "
            "where feedback_key='feedback-native-new'")) == ((15001, "16002", "500002"),)
        assert tuple(db.execute(
            "select id,workbench_task_id,workbench_turn_id from feedback_iteration_decisions "
            "where id=15002")) == ((15002, "16002", "500002"),)
        assert decision_member_rows()[-1] == ("15003", 15002, "feedback-native-new", 15001)
        assert transition_rows()[-1] == (
            "15004", 15001, "16002", "text", "500002")
        task_inverse, run_inverse = inverse(task_source, run_source, adoption_source,
            task_forward, run_forward, adoption_forward)
        inverse_owner_edges = {
            (edge["source_kind"], edge["source_physical_id"]): edge["canonical_id"]
            for edge in task_inverse["inverse_identity"]["owners"]
        }
        assert inverse_owner_edges[("workbench_tasks", 416923)] == 16002
        assert ("workbench_turns", 500002) not in inverse_owner_edges  # diagonal identity
        first_inverse_item = tuple(db.execute(
            "select rowid,feedback_key,workbench_task_id,workbench_turn_id,attempt_id,"
            "agent_run_id,current_round_id from (" +
            task12_projection("feedback_processing_items", include_rowid=True) + ") "
            "where feedback_key='feedback-native-new'"))
        assert first_inverse_item[0][0:2] == (229, "feedback-native-new")
        first_inverse_transition = tuple(db.execute(
            "select json_extract(payload_json,'$[6]'),json_extract(payload_json,'$[7]') "
            "from actions where legacy_key='15004' and record_type='feedback_processing_transitions'"))[0]
        assert first_inverse_item[0] == (
            229, "feedback-native-new", *first_inverse_transition, 44, 13003, 15001)
        assert tuple(db.execute(
            "select legacy_key,json_extract(payload_json,'$[1]'),"
            "json_extract(payload_json,'$[6]'),json_extract(payload_json,'$[7]') "
            "from actions where record_type='feedback_processing_transitions' "
            "order by cast(legacy_key as integer)")) == (
            ("8", 229, "wb-task-a", "wb-turn-a"),
            ("9", 229, "1202", "unknown-turn"),
            ("10", 229, "", ""),
            ("15004", 15001, *first_inverse_transition))
        assert decision_member_rows() == (
            ("5", 7, "feedback-a", 229),
            ("6", 227, "feedback-a", 229),
            ("15003", 15002, "feedback-native-new", 15001))

        assert decision_member_rows()[-1] == ("15003", 15002, "feedback-native-new", 15001)
        assert transition_rows()[-1] == (
            "15004", 15001, first_inverse_transition[0], "text", first_inverse_transition[1])
        assert tuple(db.execute(
            "select rowid,workbench_task_id,workbench_turn_id from (" +
            task12_projection("feedback_processing_rounds", include_rowid=True) + ") "
            "where feedback_key='feedback-native-new'")) == (
            (15001, first_inverse_transition[0], first_inverse_transition[1]),)
        assert tuple(db.execute(
            "select rowid,workbench_task_id,workbench_turn_id from (" +
            task12_projection("feedback_iteration_decisions", include_rowid=True) + ") "
            "where rowid=15002")) == (
            (15002, first_inverse_transition[0], first_inverse_transition[1]),)

        # Cycle two repeats the full portable converter chain and inverse.
        task_source = capture_task_entities(db)
        run_source = capture_run_entities(db)
        adoption_source = capture_run_adoptions(db)
        task_forward, run_forward, adoption_forward = forward(
            task_source, run_source, adoption_source, task_inverse, run_inverse)
        item_native = tuple(db.execute(
            "select item.task_id,item.feedback_key,item.workbench_task_id,"
            "item.workbench_turn_id,item.attempt_id,item.agent_run_id,item.current_round_id "
            "from feedback_processing_items item order by item.feedback_key"))
        new_item_task_id = db.execute(
            "select task_id from feedback_processing_items where feedback_key='feedback-native-new'"
        ).fetchone()[0]
        new_round_id = db.execute(
            "select id from feedback_processing_rounds where feedback_key='feedback-native-new'"
        ).fetchone()[0]
        new_decision_id = db.execute(
            "select id from feedback_iteration_decisions where id=15002").fetchone()[0]
        assert (new_item_task_id, new_round_id, new_decision_id) == (500001, 15001, 15002)
        assert item_native == ((228, "feedback-a", "202", "203", 0, 0, 0),
                               (500001, "feedback-native-new", "16002", "500002", 44, 13003, 15001))
        assert decision_member_rows() == (
            ("5", 416922, "feedback-a", 1229),
            ("6", 227, "feedback-a", 1229),
            ("15003", 15002, "feedback-native-new", 15001))
        assert transition_rows()[-1] == (
            "15004", 15001, "16002", "text", "500002")
        task_inverse, run_inverse = inverse(task_source, run_source, adoption_source,
            task_forward, run_forward, adoption_forward)
        item_source = tuple(db.execute(
            "select rowid,feedback_key,workbench_task_id,"
            "workbench_turn_id,attempt_id,agent_run_id,current_round_id "
            "from (" + task12_projection("feedback_processing_items", include_rowid=True) + ") "
            "order by feedback_key"))
        assert item_source == ((228, "feedback-a", "202", "203", 0, 0, 0),
                               first_inverse_item[0])
        assert tuple(db.execute(
            "select rowid,workbench_task_id,workbench_turn_id "
            "from (" + task12_projection("feedback_iteration_decisions", include_rowid=True) + ") "
            "order by rowid")) == (
            (7, "202", "203"), (227, "public-task", "public-turn"),
            (15002, first_inverse_transition[0], first_inverse_transition[1]))
        assert tuple(db.execute(
            "select rowid,workbench_task_id,workbench_turn_id from (" +
            task12_projection("feedback_processing_rounds", include_rowid=True) + ") "
            "where feedback_key='feedback-native-new'")) == (
            (15001, first_inverse_transition[0], first_inverse_transition[1]),)
        assert decision_member_rows()[-1] == ("15003", 15002, "feedback-native-new", 15001)
        assert transition_rows()[-1] == (
            "15004", 15001, first_inverse_transition[0], "text", first_inverse_transition[1])
    finally:
        db.close()


def _stored_columns(catalog):
    sql = next(row[2] for row in catalog if row[0] == "table" and row[1] == "actions")
    with sqlite3.connect(":memory:") as db:
        db.execute(sql)
        return tuple(row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] == 0)


def _select_columns(catalog):
    sql = next(row[2] for row in catalog if row[0] == "table" and row[1] == "actions")
    with sqlite3.connect(":memory:") as db:
        db.execute(sql)
        return tuple(row[1] for row in db.execute("pragma table_xinfo(actions)") if row[6] != 1)


def test_actual_retained_rows_roundtrip_typed_run_task_feedback_and_payload_facts():
    db, context, _, task_inverse, run_inverse = _inverse_fixture(
        feedback=True, generic_new=True, task_delivery=True)
    try:
        selected_columns = _select_columns(context["adoption_source"]["action_catalog"])
        source_columns = _stored_columns(context["adoption_source"]["action_catalog"])
        source_rows = [dict(zip(selected_columns, row, strict=True)) for row in
                       context["source_snapshot"]["rows"][2][1]]
        retired_kinds = {"review_candidates", "candidate_reviews", "candidate_selections",
                         "candidate_supplements", "candidate_executions",
                         "candidate_action_attempts", "candidate_execution_alias"}
        expected = [{name: row[name] for name in source_columns}
                    for row in source_rows if row["record_type"] not in retired_kinds]
        before_actions = [tuple(row) for row in db.execute("select * from actions order by id")]
        before_sequences = [tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")]
        actual = retained_action_source_rows(db, context["adoption_source"],
            task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert actual[:-3] == expected
        assert actual[-1]["td_reply_task_id"] == 201
        new_actions = [row for row in actual if row["id"] > max(item["id"] for item in expected)
                       and row["_kind"] == ACTION_RECORD_TYPES.code("action")]
        assert [db.execute("select json_extract(?, '$.agent_run_id')", (row["action_data"],)).fetchone()[0]
                for row in new_actions] == [9006, 0]
        decision_kind = ACTION_RECORD_TYPES.code("feedback_iteration_decision_items")
        feedback_rows = [row for row in actual if row["_kind"] == decision_kind]
        assert [(row["fb_decision_id"], row["fb_round_id"], row["fb_feedback_key"])
                for row in feedback_rows] == [(7, 229, "feedback-a"), (227, 229, "feedback-a")]
        assert all(row["task_id"] is None and row["audit_run_id"] is None for row in actual)
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before_actions
        assert [tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")] == before_sequences
    finally:
        db.close()


@pytest.mark.parametrize("remap_tasks", [False, True], ids=["meeting-key-only", "task-owner-and-meeting-key"])
def test_adoption_inverse_consumes_real_current_task_inverse_handoff(remap_tasks):
    from copy import deepcopy
    from app.run_adoption_inverse import _task_inverse_owners
    from app.task_entity_schema import _validate_inverse_identity

    db, context, forward, task_inverse, run_inverse = _inverse_fixture(remap_tasks=remap_tasks)
    try:
        raw = task_inverse["inverse_identity"]
        owners = _task_inverse_owners(task_inverse)
        assert owners == _validate_inverse_identity(raw)["owners"]
        assert set(owners) == {("reply_tasks", 201)} if remap_tasks else owners == {}
        assert len(raw["meeting_business_keys"]) >= 1
        assert task_inverse["source_context"]["handoff_required"] is True
        tampered = deepcopy(task_inverse)
        tampered["inverse_identity"]["meeting_business_keys"][0]["task_business_key_row_id"] += 1
        with pytest.raises(ValueError, match="digest mismatch"):
            _task_inverse_owners(tampered)
    finally:
        db.close()


def test_positive_missing_run_reference_rejects_without_mutating_native_actions():
    db, context, forward, task_inverse, run_inverse = _inverse_fixture(task_delivery=True)
    try:
        before = [tuple(row) for row in db.execute("select * from actions order by id")]
        edges = [edge for edge in run_inverse["run_handoff"]["identity_edges"]
                 if not (edge["source_kind"] == "agent_runs" and edge["canonical_id"] == 9106)]
        from app.run_entity_schema import run_handoff_digest
        run_inverse["run_handoff"]["identity_edges"] = edges
        run_inverse["run_handoff"]["digest"] = run_handoff_digest(
            run_inverse["run_handoff"]["source_context"], edges,
            run_inverse["run_handoff"]["runtime_attempt_parent_edges"],
            run_inverse["run_handoff"]["runtime_attempt_orphan_workload_edges"],
            run_inverse["run_handoff"]["owner_highwaters"])
        with pytest.raises(ValueError, match="missing source Run owner"):
            from app.run_adoption_inverse import restore_run_adoptions
            restore_run_adoptions(db, context["adoption_source"], forward,
                task_metadata=context["task_metadata"],
                task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before
    finally:
        db.close()


def test_missing_typed_task_owner_rejects_even_if_another_kind_uses_same_id():
    db, context, forward, task_inverse, run_inverse = _inverse_fixture(
        task_delivery=True, unresolved_task=True)
    try:
        # The bad positive reference was introduced before Task/Run inverse;
        # 201 is a Reply Task source ID and 7777 has no typed source owner.
        before = [tuple(row) for row in db.execute("select * from actions order by id")]
        with pytest.raises(ValueError, match="missing typed source Task owner"):
            from app.run_adoption_inverse import restore_run_adoptions
            restore_run_adoptions(db, context["adoption_source"], forward,
                task_metadata=context["task_metadata"],
                task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before
    finally:
        db.close()


def test_current_adopted_facts_build_source_shaped_action_rows_without_writes():
    from app.current_action_schema import current_action_payloads
    from app.run_adoption_inverse import adopted_action_source_rows, plan_run_adoption_inverse
    from app.execution_owner_definitions import EXECUTION_FIELDS

    db, context, forward, task_inverse, run_inverse = _inverse_fixture()
    try:
        from app.task_lifecycle_definitions import TASK20_LAYOUTS
        consumer, = db.execute("select consumer_run_id from run_adopted_results order by consumer_run_id limit 1").fetchone()
        plan = plan_run_adoption_inverse(db, context["adoption_source"], forward,
                                         run_inverse_metadata=run_inverse)
        source_run_id = plan["source_run_physical_by_canonical"][consumer]
        task_id, run_generation = db.execute(
            "select task_id,execution_generation from runs where id=?", (source_run_id,)).fetchone()
        generation_path = TASK20_LAYOUTS["reply_tasks"].path("execution_generation")
        db.execute("update tasks set payload_json=jsonb_set(payload_json,?,json(?)) where id=?",
                   (generation_path, '"advanced-task-generation"', task_id))
        source_columns = _stored_columns(context["adoption_source"]["action_catalog"])
        before_actions = [tuple(row) for row in db.execute("select * from actions order by id")]
        before_sequences = [tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")]
        rows = adopted_action_source_rows(db, context["adoption_source"], forward,
            run_inverse_metadata=run_inverse, identity_plan=plan)
        assert all(set(row) == set(source_columns) for row in rows)
        record_types = current_action_payloads().record_types
        candidate_kind = record_types.code("review_candidates")
        review_kind = record_types.code("candidate_reviews")
        selection_kind = record_types.code("candidate_selections")
        supplement_kind = record_types.code("candidate_supplements")
        alias_kind = record_types.code("candidate_execution_alias")
        candidates = [row for row in rows if row["_kind"] == candidate_kind]
        reviews = [row for row in rows if row["_kind"] == review_kind]
        selections = [row for row in rows if row["_kind"] == selection_kind]
        supplements = [row for row in rows if row["_kind"] == supplement_kind]
        aliases = [row for row in rows if row["_kind"] == alias_kind]
        assert candidates and reviews and selections and len(supplements) == 2
        assert len(aliases) == len(candidates)
        assert all(row["legacy_key"] is not None for row in reviews + selections + supplements)
        review_by_kind = {row["legacy_key"]: row for row in reviews}
        assert "7" in review_by_kind and review_by_kind["7"]["audit_run_id"] == 9007
        assert all(row["candidate_id"] in {candidate["id"] for candidate in candidates}
                   for row in reviews + selections + supplements)
        selection_ids = {row["id"] for row in selections}
        for candidate in candidates:
            assert set(EXECUTION_FIELDS) <= set(current_action_payloads().layouts["review_candidates"].fields)
            if candidate["execution_review_id"] is not None:
                assert candidate["execution_review_id"] in {row["id"] for row in reviews}
                if candidate["execution_selection_id"] is not None:
                    assert candidate["execution_selection_id"] in selection_ids
        rebuilt = [current_action_payloads().layouts["review_candidates"].unpack(row[0])
                   for row in (db.execute("select json(?)", (candidate["payload_json"],)).fetchone()
                               for candidate in candidates)]
        assert len(rebuilt) == len(candidates)
        assert all(row["candidate_json"] and row["candidate_digest"] for row in rebuilt)
        assert all(row["execution_generation"] == run_generation for row in rebuilt)
        assert [row["candidate_json"] for row in rebuilt] == [
            db.execute("select candidate_json from run_adopted_results where consumer_run_id=?",
                       (item["canonical_run_id"],)).fetchone()[0]
            for item in plan["adoptions"] if item["source_kind"] == "review_candidates"]
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before_actions
        assert [tuple(row) for row in db.execute(
            "select rowid,name,seq from sqlite_sequence order by rowid")] == before_sequences

        # Apply only these rows to an independent source-catalog copy. This
        # exercises the captured old Actions CHECK/FK contract, not native DDL.
        scratch = sqlite3.connect(":memory:")
        try:
            scratch.deserialize(db.serialize())
            scratch.execute("pragma foreign_keys=off")
            scratch.execute("begin")
            retained = retained_action_source_rows(scratch, context["adoption_source"],
                task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
            scratch.execute("drop table actions")
            catalog = [tuple(item) for item in context["adoption_source"]["action_catalog"]]
            for _, _, sql in sorted(catalog, key=lambda item: item[0] != "table"):
                scratch.execute(sql)
            source_rows = retained + rows
            insert_columns = source_columns
            statement = ("insert into actions(" + ",".join(insert_columns) + ") values(" +
                         ",".join("?" for _ in insert_columns) + ")")
            for source_row in sorted(source_rows, key=lambda item: item["id"]):
                scratch.execute(statement, tuple(source_row[column] for column in insert_columns))
            assert scratch.execute("pragma foreign_key_check(actions)").fetchall() == []
            assert scratch.execute("select count(*) from actions where _kind in (?,?,?,?,?)",
                (candidate_kind, review_kind, selection_kind, supplement_kind, alias_kind)).fetchone()[0] == len(rows)
        finally:
            scratch.close()
    finally:
        db.close()


def test_adopted_row_plan_reassigns_a_source_physical_id_taken_by_a_current_action():
    from app.current_action_schema import current_action_payloads
    from app.run_adoption_inverse import adopted_action_source_rows, plan_run_adoption_inverse

    db, context, forward, _, run_inverse = _inverse_fixture()
    try:
        candidate_identity = next(row for row in forward["identities"]
                                  if row[0] == "review_candidates")
        occupied_id = int(candidate_identity[1])
        action_kind = current_action_payloads().record_types.code("action")
        for (trigger_name,) in db.execute(
                "select name from main.sqlite_schema where type='trigger' and tbl_name='actions'"):
            db.execute('drop trigger main."' + trigger_name.replace('"', '""') + '"')
        db.execute("insert into actions(id,_kind,action_data,payload_json) "
                   "values(?,?,jsonb('{}'),jsonb('[]'))", (occupied_id, action_kind))
        plan = plan_run_adoption_inverse(db, context["adoption_source"], forward,
                                         run_inverse_metadata=run_inverse)
        assert plan["reassigned_identity_count"] > 0
        candidate = next(item for item in plan["adoptions"]
                         if item["source_kind"] == "review_candidates"
                         and item["mapped_physical_id"] == occupied_id)
        assert candidate["physical_id"] != occupied_id
        rows = adopted_action_source_rows(db, context["adoption_source"], forward,
            run_inverse_metadata=run_inverse, identity_plan=plan)
        assert candidate["physical_id"] in {row["id"] for row in rows
            if row["_kind"] == current_action_payloads().record_types.code("review_candidates")}
    finally:
        db.close()


def test_materializer_keeps_current_action_receipts_observers_and_map_on_collision():
    from app.action_entity_storage import ACTION_KIND_CODE
    from app.run_adoption_inverse import plan_run_adoption_inverse, restore_run_adoptions

    db, context, forward, task_inverse, run_inverse = _inverse_fixture()
    try:
        candidate_identity = next(row for row in forward["identities"]
                                  if row[0] == "review_candidates")
        occupied_id = int(candidate_identity[1])
        for (trigger_name,) in db.execute(
                "select name from main.sqlite_schema where type='trigger' and tbl_name='actions'"):
            db.execute('drop trigger main."' + trigger_name.replace('"', '""') + '"')
        db.execute("insert into actions(id,_kind,legacy_key,action_data,payload_json) "
                   "values(?,?,null,jsonb('{}'),jsonb('[]'))",
                   (occupied_id, ACTION_KIND_CODE))
        from app.task_run_entity_assembly import _action_objects
        for sql in _action_objects().values():
            if sql.lstrip().lower().startswith("create trigger"):
                db.execute(sql)
        from app.action_entity_storage import add_action_observer, append_action_receipt
        first = append_action_receipt(db, occupied_id, values={
            "operation": "send", "status": "done", "provider_result_json": "{}",
            "observed_at": "collision-provider-clock"})
        second = append_action_receipt(db, occupied_id, values={
            "operation": "send", "status": "done",
            "observed_at": "collision-lineage-clock"})
        db.execute("update action_receipts set provider_receipt_id=? where id=?",
                   (first, second))
        add_action_observer(db, occupied_id, agent_run_id=0, task_id=0,
                            created_at="collision-observer-clock")
        db.execute("insert into action_entity_identity_map values(?,?,?,?)",
                   ("generic", "collision-owner", occupied_id, second))
        plan = plan_run_adoption_inverse(db, context["adoption_source"], forward,
                                         run_inverse_metadata=run_inverse)
        candidate = next(item for item in plan["adoptions"]
                         if item["source_kind"] == "review_candidates"
                         and item["mapped_physical_id"] == occupied_id)
        assert candidate["physical_id"] != occupied_id

        restore_run_adoptions(
            db, context["adoption_source"], forward,
            task_metadata=context["task_metadata"],
            task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert db.execute("select record_type from actions where id=?", (occupied_id,)).fetchone()[0] == "action"
        assert tuple(db.execute("select action_id,provider_receipt_id from action_receipts where id=?",
                                (second,)).fetchone()) == (occupied_id, first)
        assert tuple(db.execute("select action_id,agent_run_id,task_id from action_observers").fetchone()) == (
            occupied_id, 0, 0)
        assert tuple(db.execute("select action_id,receipt_id from action_entity_identity_map").fetchone()) == (
            occupied_id, second)
        assert db.execute("select id from actions where record_type='review_candidates' "
                          "and legacy_key=?", (candidate["public_id"],)).fetchone()[0] == candidate["physical_id"]
        assert db.execute("pragma foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_adopted_rows_follow_current_supplements_after_retirement_and_addition():
    from app.run_adoption_inverse import adopted_action_source_rows
    from app.current_action_schema import current_action_payloads

    db, context, forward, _, run_inverse = _inverse_fixture()
    try:
        old_ids = [row[0] for row in db.execute(
            "select id from run_adopted_supplements order by id")]
        assert len(old_ids) == 2
        db.execute("delete from run_adopted_supplements where id=?", (old_ids[-1],))
        owner, = db.execute("select consumer_run_id from run_adopted_results order by consumer_run_id limit 1").fetchone()
        db.execute("insert into run_adopted_supplements(id,consumer_run_id,instruction,created_at) "
                   "values(?,?,?,?)", (old_ids[-1] + 20, owner, "current-new-supplement", "native-supplement-clock"))
        rows = adopted_action_source_rows(db, context["adoption_source"], forward,
            run_inverse_metadata=run_inverse)
        supplement_kind = current_action_payloads().record_types.code("candidate_supplements")
        supplements = [row for row in rows if row["_kind"] == supplement_kind]
        layout = current_action_payloads().layouts["candidate_supplements"]
        values = [layout.unpack(db.execute("select json(?)", (row["payload_json"],)).fetchone()[0])
                  for row in supplements]
        assert len(supplements) == 2
        assert {row["created_at"] for row in values} >= {"native-supplement-clock"}
        assert str(old_ids[-1] + 20) in {row["legacy_key"] for row in supplements}
        assert str(old_ids[-1]) not in {row["legacy_key"] for row in supplements}
    finally:
        db.close()


def test_adopted_rows_follow_current_review_and_execution_progress_edits():
    from app.current_action_schema import current_action_payloads
    from app.run_adoption_inverse import adopted_action_source_rows

    db, context, forward, _, run_inverse = _inverse_fixture()
    try:
        review_id, = db.execute("select id from run_adopted_reviews order by id limit 1").fetchone()
        db.execute("update run_adopted_reviews set result_json=?,created_at=? where id=?",
                   ('{"review":"current"}', "current-review-clock", review_id))
        db.execute("update run_execution_progress set result_json=?,updated_at=?",
                   ('{"execution":"current"}', "current-progress-clock"))
        rows = adopted_action_source_rows(db, context["adoption_source"], forward,
            run_inverse_metadata=run_inverse)
        record_types = current_action_payloads().record_types
        review_rows = [row for row in rows if row["_kind"] == record_types.code("candidate_reviews")]
        review_payload = current_action_payloads().layouts["candidate_reviews"]
        decoded_reviews = [review_payload.unpack(db.execute("select json(?)", (row["payload_json"],)).fetchone()[0])
                           for row in review_rows]
        assert {row["result_json"] for row in decoded_reviews} >= {'{"review":"current"}'}
        candidate_rows = [row for row in rows if row["_kind"] == record_types.code("review_candidates")]
        candidate_payload = current_action_payloads().layouts["review_candidates"]
        decoded_candidates = [candidate_payload.unpack(db.execute("select json(?)", (row["payload_json"],)).fetchone()[0])
                              for row in candidate_rows]
        assert {row["execution_result_json"] for row in decoded_candidates} >= {'{"execution":"current"}'}
        assert {row["execution_updated_at"] for row in decoded_candidates} >= {"current-progress-clock"}
    finally:
        db.close()


def test_adopted_rows_include_a_new_native_review_and_run_created_before_inverse():
    from app.current_action_schema import current_action_payloads
    from app.run_adoption_inverse import adopted_action_source_rows
    from app.run_entity_storage import create_run

    created = {}

    def add_native_review(db, context, forward):
        consumer, = db.execute(
            "select consumer_run_id from run_adopted_results order by consumer_run_id limit 1").fetchone()
        candidate = db.execute("select candidate_digest from run_adopted_results where consumer_run_id=?",
                               (consumer,)).fetchone()[0]
        cursor = db.execute("select * from runs where role='audit' and parent_id=? order by id limit 1",
                            (consumer,))
        values = dict(zip((column[0] for column in cursor.description),
                          cursor.fetchone(), strict=True))
        values.pop("id")
        values["turn_attempt"] = db.execute(
            "select coalesce(max(turn_attempt),0)+1 from runs where task_id=? and role='audit' "
            "and execution_generation=? and proposal_revision=?",
            (values["task_id"], values["execution_generation"], values["proposal_revision"])).fetchone()[0]
        audit = create_run(db, values=values)
        result = '{"candidate_digest":"' + candidate + '","outcome":"approve"}'
        review_id = db.execute("insert into run_adopted_reviews "
            "(consumer_run_id,audit_run_id,candidate_digest,decision,result_json,created_at) "
            "values(?,?,?,?,?,?) returning id",
            (consumer, audit["id"], candidate, "approve", result, "new-native-review-clock")).fetchone()[0]
        created.update({"consumer": consumer, "audit": audit["id"], "review": review_id})

    db, context, forward, task_inverse, run_inverse = _inverse_fixture(before_inverse=add_native_review)
    try:
        rows = adopted_action_source_rows(db, context["adoption_source"], forward,
            run_inverse_metadata=run_inverse)
        review_code = current_action_payloads().record_types.code("candidate_reviews")
        review = next(row for row in rows if row["_kind"] == review_code
                      and row["legacy_key"] == str(created["review"]))
        run_edge = next((edge for edge in run_inverse["run_handoff"]["identity_edges"]
                         if edge["source_kind"] == "agent_runs"
                         and edge["canonical_id"] == created["audit"]), None)
        expected_audit_id = created["audit"] if run_edge is None else run_edge["source_physical_id"]
        assert review["audit_run_id"] == expected_audit_id
        assert review["candidate_id"] is not None
        payload = current_action_payloads().layouts["candidate_reviews"].unpack(
            db.execute("select json(?)", (review["payload_json"],)).fetchone()[0])
        assert payload["result_json"] == '{"candidate_digest":"' + db.execute(
            "select candidate_digest from run_adopted_reviews where id=?", (created["review"],)).fetchone()[0] + '","outcome":"approve"}'
        assert payload["created_at"] == "new-native-review-clock"
        from app.run_adoption_inverse import restore_run_adoptions
        restored = restore_run_adoptions(
            db, context["adoption_source"], forward,
            task_metadata=context["task_metadata"],
            task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert restored["adopted_count"] == len(rows)
        stored = db.execute(
            "select id,audit_run_id from actions where record_type='candidate_reviews' "
            "and legacy_key=?", (str(created["review"]),)).fetchone()
        assert stored is not None and stored["audit_run_id"] == expected_audit_id
        assert db.execute("pragma foreign_key_check").fetchall() == []
    finally:
        db.close()
