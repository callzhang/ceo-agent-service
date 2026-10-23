import pytest

from app.run_adoption_schema import _translate_weak_run_ref


def test_weak_run_reference_preserves_null_and_zero_sentinels():
    run_map = {7: 9007}

    assert _translate_weak_run_ref(run_map, None) is None
    assert _translate_weak_run_ref(run_map, 0) == 0
    assert _translate_weak_run_ref(run_map, 7) == 9007


def test_weak_run_reference_rejects_unresolved_positive_identity():
    with pytest.raises(ValueError, match="unresolved or ambiguous source Run reference"):
        _translate_weak_run_ref({7: 9007}, 8)


def test_selection_branch_is_derived_from_exact_bound_candidate_option():
    from app.run_adoption_schema import _selection_branch

    candidate = '{"decision_options":[{"key":"keep","value":{"x":1}}]}'
    assert _selection_branch(candidate, "keep") == {
        "key": "keep", "value": {"x": 1}
    }
    with pytest.raises(ValueError, match="occur exactly once"):
        _selection_branch(candidate, "missing")


def test_action_sequence_restores_source_highwater_after_retired_maximum_is_removed():
    import sqlite3

    from app.run_adoption_schema import _restore_sequence_rows

    db = sqlite3.connect(":memory:")
    db.execute("create table actions(id integer primary key autoincrement, record_type text)")
    db.execute("insert into actions(id,record_type) values(7,'retained'),(101,'retired')")
    db.execute("delete from actions where id=101")
    captured = [tuple(row) for row in db.execute(
        "select rowid,name,seq from sqlite_sequence where name='actions'")]
    assert captured == [(1, "actions", 101)]
    db.execute("drop table actions")
    db.execute("create table actions(id integer primary key autoincrement, record_type text)")
    db.execute("insert into actions(id,record_type) values(7,'retained')")

    _restore_sequence_rows(db, captured, ("actions",))
    new_id = db.execute("insert into actions(record_type) values('new') returning id").fetchone()[0]

    assert new_id == 102


def _source_snapshot(db):
    tables = ("tasks", "runs", "actions", "action_receipts", "action_observers")
    catalog = tuple(db.execute(
        "select type,name,sql from main.sqlite_schema where tbl_name in "
        "('tasks','runs','actions','action_receipts','action_observers') "
        "and type in ('table','index','trigger') and name not like 'sqlite_%' order by type,name"))
    rows = tuple((table, tuple(tuple(row) for row in db.execute(f'select * from main."{table}" order by rowid')))
                 for table in tables)
    sequences = tuple(db.execute(
        "select rowid,name,seq from main.sqlite_sequence where name in "
        "('tasks','runs','actions','action_receipts','action_observers') order by rowid"))
    return {"catalog": catalog, "rows": rows, "sequences": sequences}


def _populated_source(*, remap_runs=False, feedback=False, pre_capture_unknown=False,
                      unresolved_action=False, stop_before_adoption=False, remap_tasks=False,
                      feedback_collision=False, generic_action_task_id=None,
                      nonagent_run_owner=False):
    """Build the actual old Task/Run/Action source with its production writers."""
    import hashlib
    import json

    from app.action_entity_storage import add_action_observer, append_action_receipt, create_action
    from app.current_database_schema import create_current_database_schema
    from app.execution_owner_storage import create_plan_execution
    from app.run_adoption_schema import capture_run_adoptions, migrate_run_adoptions
    from app.run_entity_schema import capture_run_entities, migrate_run_entities
    from app.shared_action_lifecycle import _insert, persist_plan
    from app.task_entity_schema import capture_task_entities, migrate_task_entities
    from tests.test_run_entity_schema import _run_handoff, _source_row
    from tests.test_task_entity_schema import _source as task_source

    db = task_source()
    create_current_database_schema(db)
    db.commit()
    db.execute("pragma foreign_keys=off")
    db.execute("begin immediate")
    generation = "initial"  # The current source Reply Task's real default.
    common = {
        "task_id": 201, "execution_generation": generation, "proposal_revision": 0,
        "turn_attempt": 0, "status": "completed", "session_id": "session-a",
        "transcript_start": 0, "transcript_end": 0, "structured_error_json": "{}",
        "lease_owner": "", "lease_expires_at": "", "started_at": "start",
        "completed_at": "done", "created_at": "created", "updated_at": "updated",
    }
    _source_row(db, "agent_runs", {"id": 9006, "legacy_key": "9006", **common,
        "operation_id": "consumer-op", "role": "consumer", "parent_id": None})
    _source_row(db, "agent_runs", {"id": 9007, "legacy_key": "9007", **common,
        "operation_id": "audit-op", "role": "audit", "parent_id": 9006})
    _source_row(db, "agent_runs", {"id": 9010, "legacy_key": "9010", **common,
        "operation_id": "consumer-op-2", "turn_attempt": 1,
        "role": "consumer", "parent_id": None})
    _source_row(db, "agent_runs", {"id": 9011, "legacy_key": "9011", **common,
        "operation_id": "audit-op-2", "turn_attempt": 1,
        "role": "audit", "parent_id": 9010})
    if nonagent_run_owner:
        _source_row(db, "agent_runtime_attempts", {
            "id": 9008, "legacy_key": "9008", "parent_id": None,
            "workload_kind": "task", "workload_key": "201:custom", "attempt_number": 1,
            "route_name": "r-9008", "runtime_kind": "codex_cli", "credential_mode": "local_oauth",
            "model": "m", "session_mode": "fresh", "source_session_id": "",
            "attempt_purpose": "normal", "validation_retry_policy_id": "",
            "validation_result_schema_id": "", "session_id": "s", "transcript_reference": "t",
            "transcript_start": 0, "transcript_end": 0, "status": "completed",
            "failure_class": "", "failure_code": "", "failover_permitted": 0,
            "lease_owner": "", "lease_expires_at": "", "first_effect_started_at": "",
            "result_schema_id": "schema", "started_at": "start", "finished_at": "finish",
            "created_at": "created", "updated_at": "updated",
        })

    if feedback:
        # Distinct physical and public IDs prove the converter resolves the
        # typed Task public domain rather than copying a coincident integer.
        from app.task12_storage import task12_insert_parts
        decision = task12_insert_parts(db, "feedback_iteration_decisions", {
            "id": 7, "batch_id": "batch-a", "workbench_task_id": "202",
            "workbench_turn_id": "203", "decision_json": "{}",
        })
        assert decision.source_rowid == 7 and decision.physical_id != 7
        db.execute(decision.sql, decision.parameters)
        if feedback_collision:
            collided = task12_insert_parts(db, "feedback_iteration_decisions", {
                "id": 9, "batch_id": "batch-collision", "workbench_task_id": "204",
                "workbench_turn_id": "205", "decision_json": "{}",
            })
            collision_source_id = collided.physical_id
            db.execute(collided.sql, collided.parameters)
        from app.action_feedback_storage import insert_feedback_action
        insert_feedback_action(db, "feedback_iteration_decision_items", {
            "decision_id": 7, "feedback_key": "feedback-a", "round_id": 229,
        }, source_rowid=5)
        if not feedback_collision:
            insert_feedback_action(db, "feedback_iteration_decision_items", {
                "decision_id": 227, "feedback_key": "feedback-a", "round_id": 229,
            }, source_rowid=6)
        if feedback_collision:
            insert_feedback_action(db, "feedback_iteration_decision_items", {
                "decision_id": collision_source_id, "feedback_key": "feedback-a", "round_id": 229,
            }, source_rowid=6)

    candidate_json = json.dumps({
        "outcome": "needs_human",
        "decision_options": [{"key": "stop", "value": {"mode": "stop"}}],
    }, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(candidate_json.encode()).hexdigest()
    persist_plan(db, "reviewed", {
        "id": 40, "task_id": 201, "execution_generation": generation,
        "consumer_run_id": 9006, "stage_index": 0, "proposal_revision": 0,
        "candidate_digest": digest, "candidate_json": candidate_json,
        "created_at": "candidate-clock",
    })
    _insert(db, "candidate_reviews", {
        "id": 7, "candidate_id": 40, "audit_run_id": 9007,
        "decision": "approve", "candidate_digest": digest,
        "result_json": '{"outcome":"approve"}', "created_at": "review-clock",
    })
    _insert(db, "candidate_selections", {
        "id": 18, "candidate_id": 40, "review_id": 7,
        "option_key": "stop", "created_at": "selection-clock",
    })
    _insert(db, "candidate_supplements", {
        "id": 30, "candidate_id": 40, "instruction": "first", "created_at": "supplement-1",
    })
    _insert(db, "candidate_supplements", {
        "id": 31, "candidate_id": 40, "instruction": "second", "created_at": "supplement-2",
    })
    execution_id = create_plan_execution(db, candidate_id=40, review_id=7,
        selection_id=18, status="done", lease_owner="", lease_expires_at="")
    action = create_action(db, values={
        "business_object_key": "business-201", "action_identity": "send_notice",
        "operation": "send_group_message", "parameters_json": "{}", "agent_run_id": 9006,
    })
    if generic_action_task_id is not None:
        db.execute("update actions set task_id=? where id=?", (generic_action_task_id, action["id"]))
    append_action_receipt(db, action["id"], values={
        "execution_id": execution_id, "operation": "send_group_message",
        "target": '{"conversation_id":"chat"}', "provider_result_json": '{"ok":true}',
        "result_digest": "receipt-digest", "created_at": "receipt-clock",
    })
    second_json = json.dumps({"outcome": "no_action", "summary": "second"},
                             ensure_ascii=False, separators=(",", ":"))
    second_digest = hashlib.sha256(second_json.encode()).hexdigest()
    persist_plan(db, "reviewed", {
        "id": 41, "task_id": 201, "execution_generation": generation,
        "consumer_run_id": 9010, "stage_index": 0, "proposal_revision": 0,
        "candidate_digest": second_digest, "candidate_json": second_json,
        "created_at": "candidate-clock-2",
    })
    _insert(db, "candidate_reviews", {
        "id": 9, "candidate_id": 41, "audit_run_id": 9011,
        "decision": "approve", "candidate_digest": second_digest,
        "result_json": '{"outcome":"approve"}', "created_at": "review-clock-2",
    })
    second_execution_id = create_plan_execution(db, candidate_id=41, review_id=9,
        selection_id=None, status="done", lease_owner="", lease_expires_at="")
    append_action_receipt(db, action["id"], values={
        "execution_id": second_execution_id, "operation": "send_group_message",
        "target": '{"conversation_id":"chat-2"}', "provider_result_json": '{"ok":true}',
        "result_digest": "receipt-digest-2", "created_at": "receipt-clock-2",
    })
    add_action_observer(db, action["id"], agent_run_id=0, task_id=0, created_at="observer-clock")
    add_action_observer(db, action["id"], agent_run_id=9007, task_id=201, created_at="observer-clock-2")
    if feedback:
        assert db.execute("select fb_decision_id from actions "
                          "where record_type='feedback_iteration_decision_items'").fetchone()[0] == 7
    if unresolved_action:
        create_action(db, values={
            "business_object_key": "business-201", "action_identity": "unresolved",
            "operation": "send_group_message", "parameters_json": "{}", "agent_run_id": 7777,
        })
    # Source facts are committed first; the migration gets the caller-owned
    # transaction whose rollback must restore this exact input state.
    db.commit()
    db.execute("begin immediate")
    source_snapshot = _source_snapshot(db)

    if pre_capture_unknown:
        db.execute("alter table actions add column unsupported_extra text")
        return db, source_snapshot

    # This is the required pre-Task2B/Run3B source capture order.
    adoption_source = capture_run_adoptions(db)
    task_source_metadata = capture_task_entities(db)
    run_source_metadata = capture_run_entities(db)
    task_handoff = None
    if remap_tasks:
        task_handoff = {"version": 1, "owners": [{
            "source_kind": "reply_tasks", "source_physical_id": 201, "canonical_id": 1201,
        }]}
    if remap_tasks and feedback_collision:
        task_handoff["owners"].append({
            "source_kind": "feedback_iteration_decisions", "source_physical_id": collision_source_id,
            "canonical_id": 10,
        })
        task_handoff["owners"].append({
            "source_kind": "feedback_iteration_decisions", "source_physical_id": decision.physical_id,
            "canonical_id": collision_source_id,
        })
    task_metadata = migrate_task_entities(db, task_source_metadata, inverse_identity=task_handoff)
    base_handoff = _run_handoff(db, run_source_metadata)
    if remap_runs:
        edges = [
            {"source_kind": "agent_runs", "source_physical_id": 9006, "canonical_id": 9106},
            {"source_kind": "agent_runs", "source_physical_id": 9007, "canonical_id": 9107},
            {"source_kind": "agent_runs", "source_physical_id": 9010, "canonical_id": 9110},
            {"source_kind": "agent_runs", "source_physical_id": 9011, "canonical_id": 9111},
        ]
        if nonagent_run_owner:
            edges.append({"source_kind": "agent_runtime_attempts",
                          "source_physical_id": 9008, "canonical_id": 9106})
        base_handoff = _run_handoff(db, run_source_metadata, identity_edges=edges,
            owner_highwaters=base_handoff["owner_highwaters"])
    run_metadata = migrate_run_entities(db, run_source_metadata, task_metadata=task_metadata,
                                        run_handoff=base_handoff)
    if stop_before_adoption:
        return db, {"adoption_source": adoption_source, "task_source_metadata": task_source_metadata,
                    "task_metadata": task_metadata, "run_source_metadata": run_source_metadata,
                    "run_metadata": run_metadata, "candidate_json": candidate_json,
                    "digest": digest, "source_snapshot": source_snapshot}
    adoption_metadata = migrate_run_adoptions(db, adoption_source,
        task_metadata=task_metadata, run_metadata=run_metadata)
    return db, task_metadata, adoption_metadata, candidate_json, digest, source_snapshot


def test_actual_old_source_forward_moves_candidate_review_selection_supplements_receipt_and_observer():
    import contextlib

    from app.store import AutoReplyStore

    db, _, result, candidate_json, digest, _ = _populated_source()
    try:
        assert db.execute("select id,audit_run_id,consumer_run_id,candidate_digest "
                          "from run_adopted_reviews").fetchone() == (7, 9007, 9006, digest)
        assert db.execute("select id,audit_run_id,option_key from run_adopted_selections").fetchone() == (
            18, 9007, "stop")
        assert tuple(db.execute("select id,instruction from run_adopted_supplements order by id")) == (
            (30, "first"), (31, "second"))
        assert tuple(db.execute("select execution_run_id,result_digest from action_receipts order by id")) == (
            (9006, "receipt-digest"), (9010, "receipt-digest-2"))
        assert tuple(db.execute("select agent_run_id,task_id from action_observers order by agent_run_id")) == (
            (0, 0), (9007, 201))
        assert db.execute("select action_agent_run_id from actions where record_type='action'").fetchone()[0] == 9006
        assert result["receipt_count"] == 2
        assert db.execute("select count(*) from run_adoption_identity_map").fetchone()[0] == 9
        assert not db.execute("select 1 from actions where record_type in "
            "('review_candidates','candidate_reviews','candidate_selections','candidate_supplements',"
            "'candidate_execution_alias','candidate_action_attempts')").fetchone()
        assert not db.execute("select 1 from temp.sqlite_schema where name like '_run_adoption_%'").fetchone()
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("pragma foreign_key_check").fetchall() == []

        store = AutoReplyStore.__new__(AutoReplyStore)
        @contextlib.contextmanager
        def connect():
            yield db
        store._connect = connect
        db.row_factory = __import__("sqlite3").Row
        adopted = store.get_review_candidate(9006)
        assert adopted["candidate_json"] == candidate_json
        assert adopted["candidate_digest"] == digest
        selection = store.get_candidate_selection(9006)
        assert selection["review_id"] == 7 and selection["audit_run_id"] == 9007
        from app.model_action_schema import selected_branch
        assert selected_branch(db, 18) == {"key": "stop", "value": {"mode": "stop"}}
        assert db.execute("select count(*) from actions where record_type='candidate_action_attempts'").fetchone()[0] == 0
    finally:
        db.close()


def test_actual_old_source_forward_maps_remapped_runs_and_typed_feedback_references():
    db, _, _, _, _, _ = _populated_source(remap_runs=True, feedback=True)
    try:
        assert tuple(db.execute("select id,task_id,parent_id from runs order by id")) == (
            (9106, 201, None), (9107, 201, 9106), (9110, 201, None), (9111, 201, 9110))
        assert db.execute("select id,audit_run_id,consumer_run_id from run_adopted_reviews").fetchone() == (
            7, 9107, 9106)
        assert tuple(db.execute("select execution_run_id from action_receipts order by id")) == ((9106,), (9110,))
        assert db.execute("select action_agent_run_id from actions where record_type='action'").fetchone()[0] == 9106
        assert tuple(db.execute("select agent_run_id,task_id from action_observers order by agent_run_id")) == (
            (0, 0), (9107, 201))
        feedback = db.execute("select fb_decision_id,fb_round_id from actions "
                              "where record_type='feedback_iteration_decision_items' and fb_decision_id<>227").fetchone()
        assert db.execute("select fb_decision_id from actions where record_type='feedback_iteration_decision_items' "
                          "and fb_decision_id=227").fetchone() == (227,)
        target_decision = db.execute("select id from feedback_iteration_decisions "
                                     "where id not in (227)").fetchone()[0]
        assert tuple(feedback) == (target_decision, 229)
        assert target_decision != 7  # Source public Review/Decision key differs from row ID.
        assert db.execute("select id from feedback_processing_rounds where id=229").fetchone()[0] == 229
        assert db.execute("pragma foreign_key_check").fetchall() == []
    finally:
        db.close()


def test_actual_old_source_forward_maps_remapped_task_owner_for_run_candidate_and_action():
    db, _, _, _, _, _ = _populated_source(remap_tasks=True, remap_runs=True)
    try:
        assert db.execute("select task_id from runs where id=9106").fetchone() == (1201,)
        assert db.execute("select runs.task_id from run_adopted_results join runs "
                          "on runs.id=run_adopted_results.consumer_run_id where runs.id=9106").fetchone() == (1201,)
        assert tuple(db.execute("select task_id from action_observers where agent_run_id=9107")) == ((1201,),)
        assert db.execute("select id,audit_run_id from run_adopted_reviews").fetchone() == (7, 9107)
    finally:
        db.close()


def test_feedback_native_id_collision_without_typed_source_owner_rolls_back():
    import pytest

    from app.run_adoption_schema import migrate_run_adoptions

    db, context = _populated_source(remap_tasks=True, feedback=True, feedback_collision=True,
                                    stop_before_adoption=True)
    try:
        physical_collision = db.execute(
            "select fb_decision_id from temp._run_adoption_all_source_actions "
            "where record_type='feedback_iteration_decision_items' and fb_feedback_key='feedback-a' "
            "and fb_decision_id not in (7,227)"
        ).fetchone()[0]
        feedback_edges = [edge for edge in context["task_metadata"]["identity_edges"]
                          if edge["source_kind"] == "feedback_iteration_decisions"]
        assert any(edge["public_key"] == 9 and edge["target_id"] == 10
                   for edge in feedback_edges)
        assert any(edge["target_id"] == physical_collision for edge in feedback_edges)
        assert not any(edge["public_key"] == physical_collision for edge in feedback_edges)
        with pytest.raises(ValueError, match="unresolved feedback_iteration_decisions public identity"):
            migrate_run_adoptions(db, context["adoption_source"], task_metadata=context["task_metadata"],
                                  run_metadata=context["run_metadata"])
        db.rollback()
        assert _source_snapshot(db) == context["source_snapshot"]
    finally:
        db.close()


def test_retained_generic_action_task_reference_is_rejected_before_target_loss():
    import pytest

    from app.run_adoption_schema import migrate_run_adoptions

    db, context = _populated_source(remap_tasks=True, generic_action_task_id=201,
                                    stop_before_adoption=True)
    try:
        with pytest.raises(ValueError, match="retained source Action task_id has no assembled target representation"):
            migrate_run_adoptions(db, context["adoption_source"], task_metadata=context["task_metadata"],
                                  run_metadata=context["run_metadata"])
        db.rollback()
        assert _source_snapshot(db) == context["source_snapshot"]
    finally:
        db.close()


def _assert_original_source_restored(db, snapshot):
    assert _source_snapshot(db) == snapshot
    assert db.execute("select name from temp.sqlite_schema where name like '_run_adoption_%'").fetchall() == []


def test_actual_source_unknown_action_catalog_rejects_before_temp_capture():
    from app.run_adoption_schema import capture_run_adoptions

    db, snapshot = _populated_source(pre_capture_unknown=True)
    try:
        with pytest.raises(ValueError, match="unsupported current Action declaration"):
            capture_run_adoptions(db)
        assert db.execute("select name from temp.sqlite_schema where name like '_run_adoption_%'").fetchall() == []
        db.rollback()
        _assert_original_source_restored(db, snapshot)
    finally:
        db.close()


def test_actual_source_target_collision_rejects_and_caller_rollback_restores_everything():
    from app.run_adoption_schema import migrate_run_adoptions

    db, staged = _populated_source(stop_before_adoption=True)
    try:
        db.execute("create table run_adopted_results(id integer)")
        with pytest.raises(ValueError, match="adoption target name already occupied"):
            migrate_run_adoptions(db, staged["adoption_source"],
                task_metadata=staged["task_metadata"], run_metadata=staged["run_metadata"])
        db.rollback()
        _assert_original_source_restored(db, staged["source_snapshot"])
    finally:
        db.close()


def test_actual_source_catalog_drift_after_capture_rejects_and_rolls_back():
    from app.run_adoption_schema import migrate_run_adoptions

    db, staged = _populated_source(stop_before_adoption=True)
    try:
        db.execute("alter table actions add column unsupported_extra text")
        with pytest.raises(ValueError, match="current source actions catalog changed after capture"):
            migrate_run_adoptions(db, staged["adoption_source"],
                task_metadata=staged["task_metadata"], run_metadata=staged["run_metadata"])
        db.rollback()
        _assert_original_source_restored(db, staged["source_snapshot"])
    finally:
        db.close()


def test_actual_source_unresolved_positive_action_run_reference_fails_and_rolls_back():
    from app.run_adoption_schema import migrate_run_adoptions

    db, staged = _populated_source(unresolved_action=True, stop_before_adoption=True)
    try:
        with pytest.raises(ValueError, match="unresolved or ambiguous source Run reference"):
            migrate_run_adoptions(db, staged["adoption_source"],
                task_metadata=staged["task_metadata"], run_metadata=staged["run_metadata"])
        db.rollback()
        _assert_original_source_restored(db, staged["source_snapshot"])
    finally:
        db.close()


def test_actual_source_successful_forward_remains_caller_rollbackable_with_allocator_restore():
    db, _, _, _, _, snapshot = _populated_source()
    try:
        assert db.execute("select 1 from run_adopted_reviews where id=7").fetchone()
        db.rollback()
        _assert_original_source_restored(db, snapshot)
    finally:
        db.close()
