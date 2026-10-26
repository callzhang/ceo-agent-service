from contextlib import closing
import json
import sqlite3

from app.task_entity_schema import capture_task_entities, migrate_task_entities, restore_task_entities
from app.task_entity_storage import get_meeting_delivery_key, list_task_business_keys
from tests.test_task_entity_schema import _source
from app.task12_storage import insert_task12


def test_meeting_delivery_key_survives_two_portable_inverse_cycles_and_old_allocator_write():
    with closing(_source()) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata, inverse_identity={
            "version": 1,
            "owners": [{"source_kind": "meeting_alignment_jobs",
                        "source_physical_id": 221, "canonical_id": 1221}],
        })
        key = get_meeting_delivery_key(db, 1221)
        assert key == "meeting-alignment:221:meeting"
        key_row_id = list_task_business_keys(db, 1221)[0]["id"]
        assert db.execute("select count(*) from task_business_keys where task_id=201").fetchone()[0] == 1

        inverse = restore_task_entities(db, source_metadata, forward)
        assert inverse["inverse_identity"]["meeting_business_keys"] == [{
            "source_meeting_physical_id": 221,
            "task_business_key_row_id": key_row_id,
        }]
        assert db.execute(
            "select id from tasks where record_type='meeting_alignment_jobs'"
        ).fetchone()[0] == 221
        assert db.execute(
            "select count(*) from tasks where record_type='business_object_tasks'"
        ).fetchone()[0] == 1
        new_old_key = insert_task12(db, "business_object_tasks", {
            "reply_task_id": 201,
            "business_object_key": "old-write-between-cycles",
        })
        assert new_old_key.physical_id > key_row_id

        second_source_metadata = capture_task_entities(db)
        identity = json.loads(json.dumps(inverse["inverse_identity"]))
        context = json.loads(json.dumps(inverse["source_context"]))
        second_forward = migrate_task_entities(
            db, second_source_metadata, source_context=context, inverse_identity=identity,
        )
        assert get_meeting_delivery_key(db, 1221) == key
        assert {row["business_key"] for row in list_task_business_keys(db, 201)} == {
            "business-224", "old-write-between-cycles",
        }
        second_inverse = restore_task_entities(db, second_source_metadata, second_forward)
        assert second_inverse["inverse_identity"]["meeting_business_keys"] == [{
            "source_meeting_physical_id": 221,
            "task_business_key_row_id": key_row_id,
        }]


def test_meeting_delivery_key_lookup_requires_exactly_one_native_meeting_association():
    with closing(_source()) as db:
        db.row_factory = sqlite3.Row
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        migrate_task_entities(db, metadata)
        assert get_meeting_delivery_key(db, 221) == "meeting-alignment:221:meeting"

        db.execute("delete from task_business_keys where task_id=221")
        try:
            get_meeting_delivery_key(db, 221)
        except ValueError as exc:
            assert "exactly one" in str(exc)
        else:
            raise AssertionError("missing Meeting delivery identity was accepted")


def test_deleted_source_meeting_does_not_replay_stale_delivery_key_edge():
    with closing(_source()) as db:
        db.execute("begin immediate")
        source_metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, source_metadata)
        inverse = restore_task_entities(db, source_metadata, forward)
        # The legacy Meeting family has no dedicated delete DAO; model the
        # actual old-source retirement as a row deletion inside its transaction.
        assert db.execute(
            "delete from tasks where record_type='meeting_alignment_jobs' and id=221"
        ).rowcount == 1

        retired_source = capture_task_entities(db)
        migrated = migrate_task_entities(
            db, retired_source, source_context=inverse["source_context"],
            inverse_identity=json.loads(json.dumps(inverse["inverse_identity"])),
        )
        assert db.execute("select 1 from tasks where id=221 and kind='meeting_alignment'").fetchone() is None
        assert db.execute(
            "select 1 from task_business_keys where business_key='meeting-alignment:221:meeting'"
        ).fetchone() is None
        assert migrated["counts"]["meeting_alignment_jobs"] == 0


def test_meeting_key_row_collision_is_rejected_before_source_schema_replacement():
    with closing(_source()) as db:
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        before = db.execute("select id,record_type from tasks order by id").fetchall()
        try:
            migrate_task_entities(db, metadata, inverse_identity={
                "version": 1, "owners": [],
                "meeting_business_keys": [{
                    "source_meeting_physical_id": 221,
                    "task_business_key_row_id": 224,
                }],
            })
        except ValueError as exc:
            assert "collides" in str(exc)
        else:
            raise AssertionError("Meeting key-row collision was accepted")
        assert db.execute("select id,record_type from tasks order by id").fetchall() == before


def test_inverse_rejects_meeting_key_that_no_longer_matches_meeting_source_facts():
    with closing(_source()) as db:
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, metadata)
        db.execute("update tasks set parameters_json=jsonb_set(parameters_json,'$.meeting_id',jsonb(?)) "
                   "where id=221", ('"changed"',))
        try:
            restore_task_entities(db, metadata, forward)
        except ValueError as exc:
            assert "does not match" in str(exc)
        else:
            raise AssertionError("stale Meeting delivery identity was accepted")
        assert db.execute("select count(*) from task_business_keys").fetchone()[0] == 2


def test_inverse_rejects_meeting_key_timestamps_before_schema_replacement():
    with closing(_source()) as db:
        db.execute("begin immediate")
        metadata = capture_task_entities(db)
        forward = migrate_task_entities(db, metadata)
        db.execute("update task_business_keys set created_at='2026-10-23T00:00:00Z' where task_id=221")
        tasks_before = db.execute("select id,kind from tasks order by id").fetchall()
        keys_before = db.execute("select id,task_id,business_key,created_at,updated_at from task_business_keys order by id").fetchall()
        try:
            restore_task_entities(db, metadata, forward)
        except ValueError as exc:
            assert "timestamps" in str(exc)
        else:
            raise AssertionError("Meeting association timestamp loss was accepted")
        assert db.execute("select id,kind from tasks order by id").fetchall() == tasks_before
        assert db.execute("select id,task_id,business_key,created_at,updated_at from task_business_keys order by id").fetchall() == keys_before

