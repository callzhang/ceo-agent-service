"""Selection declaration and current reader controls."""

import sqlite3

import pytest

from app import action_storage as actions
from app.action_email_storage import _columns, _fingerprint
from app.email_canonical_storage import _action_objects
from app.reviewed_candidate_store import _json
from app.storage_payload import ACTION_RECORD_TYPES
from tests.support.historical_action_storage import (
    db as db,
    insert_historical_reply,
    seed_historical_task,
)


SOURCE_HASHES = {
    "actual_no_memory_fk": "a72296e1cf7c55dda83b63cff6e1760bfe6ffeb09226450225f100f40501ec66",
    "fresh_memory_fk": "e57ae120f24d6ab8aa6e8b5d9825101f438222bfbbbf14060a851d2242fe4245",
}
APPROVE = {"key": "approve", "plan": {"actions": [{"operation": "old-bound"}]}}
STOP = {"key": "stop", "terminal_outcome": "skipped"}


def _add(db, kind, physical_id, public_id, values, *, references=None, target=False):
    variant = actions._reply_variant_for(db)
    options = {"physical_provenance": True}
    if target:
        options["selection_references"] = True
    payload = actions.action_payloads(variant, **options).pack(kind, values)
    data = {"id": physical_id, "_kind": ACTION_RECORD_TYPES.code(kind),
            "legacy_key": str(public_id), **(references or {}),
            "payload_json": db.execute("select jsonb(?)", (payload,)).fetchone()[0]}
    db.execute("insert into actions(" + ",".join(data) + ") values("
               + ",".join("?" for _ in data) + ")", tuple(data.values()))


def _seed(db, *, options=None, branch=None):
    from app.run_storage import insert_run
    seed_historical_task(db)
    for run_id in (2, 3, 4):
        insert_run(db, "agent_runs", dict(id=run_id, task_id=1, proposal_revision=run_id,
            execution_generation="generation", role="audit" if run_id % 2 == 0 else "consumer"))
    for physical, public, run, choices in (
        (101, 11, 1, [APPROVE, STOP] if options is None else options),
        (202, 22, 3, [{"key": "approve", "plan": {"actions": [{"operation": "newer"}]}}, STOP]),
    ):
        _add(db, "review_candidates", physical, public, dict(
            execution_generation="generation", stage_index=0, proposal_revision=public,
            candidate_digest=f"digest-{public}", candidate_json=_json({"decision_options": choices}),
            invalidated_at="", invalidation_reason="", created_at="candidate-clock"),
            references={"task_id": 1, "consumer_run_id": run})
    for physical, public, candidate, run in ((301, 31, 101, 2), (302, 32, 202, 4)):
        _add(db, "candidate_reviews", physical, public, dict(decision="approve",
            candidate_digest="digest", result_json="{}", created_at="review-clock"),
            references={"candidate_id": candidate, "audit_run_id": run})
    _add(db, "candidate_selections", 501, 51, dict(option_key="approve",
        branch_json=_json(APPROVE) if branch is None else branch,
        status="selected", created_at="selection-clock"),
        references={"candidate_id": 101, "review_id": 301})
    _add(db, "candidate_executions", 601, 61, dict(status="done", lease_owner="",
        lease_expires_at="", result_json=' { "kept": 1 } ', created_at="created", updated_at="updated"),
        references={"candidate_id": 101, "review_id": 301, "selection_id": 501})
    actions.insert_error_action(db, dict(kind="worker", detail=b"\x00\xff"))
    insert_historical_reply(db, dict(conversation_id="chat",
        conversation_title="title", trigger_message_id="input", trigger_sender="person",
        trigger_text="text", action="reply", sensitivity_kind="none", send_status="pending",
        retry_count=-0.0))
    # The DAO applies original INTEGER affinity first. Inject a valid encoded
    # negative zero to prove that migration itself does not re-encode scalars.
    path = actions.action_payloads(actions._reply_variant_for(db)).path("reply_attempts", "retry_count")
    db.execute("update actions set payload_json=jsonb_set(payload_json,?,jsonb(?)) where record_type='reply_attempts'",
               (path, _json(actions._encoded_native_sqlite_value(-0.0))))
    db.execute("insert into sqlite_sequence(name,seq) values('actions:unrelated-water',987654)")


def _historical_selected_branch(db, selection_id):
    from app.model_action_schema import candidate_option

    row = db.execute("""select c.candidate_json,s.option_key
        from actions s join actions c
          on c.id=s.candidate_id and c.record_type='review_candidates'
        where s.record_type='candidate_selections' and s.legacy_key=?""",
        (str(selection_id),)).fetchone()
    if row is None:
        raise ValueError("selection or its exact candidate is missing")
    return candidate_option(row[0], row[1])


def _raw(db):
    columns = _columns(db, "actions")
    return [tuple(row) for row in db.execute("select " + ",".join(columns)
                                             + " from actions order by id")]


def _snapshot(db):
    return (actions._objects(db, "actions"), _raw(db),
            [tuple(row) for row in db.execute("select rowid,name,seq from sqlite_sequence order by rowid")])


def test_frozen_source_factories_and_opt_in_target():
    assert _fingerprint(actions._current_objects()) == "21a82e3a36ed0cc4043cb52f91346b9be3aa116d086d8e28b0dcc8b03f98a5ec"
    for variant, digest in SOURCE_HASHES.items():
        assert _fingerprint(_action_objects(variant, target=True, inline_legacy_owner=True,
            physical_provenance=True, compact_payload=True)) == digest
        source = actions.action_payloads(variant, physical_provenance=True)
        target = actions.action_payloads(variant, physical_provenance=True, selection_references=True)
        assert source.layouts["candidate_selections"].fields == ("option_key", "branch_json", "status", "created_at")
        assert target.layouts["candidate_selections"].fields == ("option_key", "status", "created_at")
        assert {k: v for k, v in source.layouts.items() if k != "candidate_selections"} == {
            k: v for k, v in target.layouts.items() if k != "candidate_selections"}


def test_target_constructor_rejects_old_reserved_slot(db):
    from app.email_canonical_storage import ACTION_OPTIONS
    variant = actions._reply_variant_for(db)
    db.execute("drop table actions")
    options = dict(reply_variant=variant, canonical_email=True, inline_legacy_owner=True,
                   physical_provenance=True, compact_payload=True,
                   selection_references=True, **ACTION_OPTIONS)
    actions.create_action_schema(db, **options)
    assert actions.action_schema_is_current(db, **options)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _add(db, "candidate_selections", 1, 1, dict(option_key="approve", branch_json="{}",
            status="selected", created_at="clock"), references={"candidate_id": 8, "review_id": 9})
    _add(db, "candidate_selections", 1, 1, dict(option_key="approve", status="selected", created_at="clock"),
         references={"candidate_id": 8, "review_id": 9}, target=True)
    assert db.execute("select json_array_length(payload_json) from actions").fetchone() == (3,)
