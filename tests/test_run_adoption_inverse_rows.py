import sqlite3

import pytest

from app.run_adoption_inverse import retained_action_source_rows
from app.storage_payload import ACTION_RECORD_TYPES


def _inverse_fixture(*, feedback=False, generic_new=False, task_delivery=False,
                     unresolved_task=False):
    from app.run_adoption_schema import migrate_run_adoptions
    from app.run_entity_schema import restore_run_entities
    from app.task_entity_schema import restore_task_entities
    from tests.test_run_adoption_schema import _populated_source

    db, context = _populated_source(remap_tasks=True, remap_runs=True,
                                    feedback=feedback, stop_before_adoption=True)
    db.row_factory = sqlite3.Row
    forward = migrate_run_adoptions(db, context["adoption_source"],
        task_metadata=context["task_metadata"], run_metadata=context["run_metadata"])
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


def test_positive_missing_run_reference_rejects_without_mutating_native_actions():
    db, context, _, task_inverse, run_inverse = _inverse_fixture(task_delivery=True)
    try:
        before = [tuple(row) for row in db.execute("select * from actions order by id")]
        edges = [edge for edge in run_inverse["run_handoff"]["identity_edges"]
                 if not (edge["source_kind"] == "agent_runs" and edge["canonical_id"] == 9106)]
        from app.run_entity_schema import run_handoff_digest
        run_inverse["run_handoff"]["identity_edges"] = edges
        run_inverse["run_handoff"]["digest"] = run_handoff_digest(
            run_inverse["run_handoff"]["source_context"], edges,
            run_inverse["run_handoff"]["owner_highwaters"])
        with pytest.raises(ValueError, match="missing source Run owner"):
            retained_action_source_rows(db, context["adoption_source"],
                task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before
    finally:
        db.close()


def test_missing_typed_task_owner_rejects_even_if_another_kind_uses_same_id():
    db, context, _, task_inverse, run_inverse = _inverse_fixture(
        task_delivery=True, unresolved_task=True)
    try:
        # The bad positive reference was introduced before Task/Run inverse;
        # 201 is a Reply Task source ID and 7777 has no typed source owner.
        before = [tuple(row) for row in db.execute("select * from actions order by id")]
        with pytest.raises(ValueError, match="missing typed source Task owner"):
            retained_action_source_rows(db, context["adoption_source"],
                task_inverse_metadata=task_inverse, run_inverse_metadata=run_inverse)
        assert [tuple(row) for row in db.execute("select * from actions order by id")] == before
    finally:
        db.close()
