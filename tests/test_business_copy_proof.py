"""Synthetic combined-copy proof for four Business kinds and retained children."""

from contextlib import closing

import pytest

from app.business_schema_release import migrate_business_schema, restore_business_schema
from app.business_storage import delete_business, insert_business, update_business
from scripts import validate_action_email_copy as proof
from test_business_schema_release import _seed_complete_source, _source_db


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_business_image_tracks_current_facts_through_inverse_and_reforward(variant):
    with closing(_source_db(variant)) as db:
        _seed_complete_source(db)
        source = proof._business_source_image(db)
        assert len(source["parents"]) == 4
        assert len(source["children"]) == 15
        assert len(source["parent_waters"]) + len(source["child_waters"]) == 13
        db.execute("begin immediate")
        inverse = migrate_business_schema(db)
        target = proof._business_target_image(db, variant)
        proof._assert_business_facts_equal(source, target)
        db.commit()

        db.execute("begin immediate")
        insert_business(db, "anchor", {"id": 123, "anchor_type": "project",
                                       "anchor_ref": "new a", "title": "new a"})
        insert_business(db, "anchor", {"id": 124, "anchor_type": "project",
                                       "anchor_ref": "new b", "title": "new b"})
        insert_business(db, "project", {"id": 123, "canonical_anchor_id": 123,
                                        "title": "new project", "registry_source": "test"})
        insert_business(db, "project_context_revision", {
            "id": 123, "project_id": 123, "context_json": "{}", "evidence_json": "[]",
        })
        insert_business(db, "task", {"id": 123, "title": "new task",
                                     "stage": "candidate"})
        insert_business(db, "task", {"id": 124, "title": "deleted task",
                                     "stage": "candidate"})
        update_business(db, "task", 123, {"title": "edited task"})
        update_business(db, "project", 123, {"canonical_anchor_id": 124})
        delete_business(db, "task", 124)
        db.execute("update business_task_evidence set task_id=123 where rowid=81")
        db.execute("delete from business_task_events where id=-44")
        db.commit()
        current = proof._business_target_image(db, variant)
        assert current != target

        db.execute("begin immediate")
        restore_business_schema(db, inverse)
        restored = proof._business_source_image(db)
        proof._assert_business_facts_equal(current, restored)
        assert restored["declarations"] == source["declarations"]
        db.commit()

        db.execute("begin immediate")
        migrate_business_schema(db)
        proof._assert_business_facts_equal(restored, proof._business_target_image(db, variant))
        db.rollback()


@pytest.mark.parametrize("damaged_table", ("parent", "child"))
def test_business_copy_rejects_changes_to_original_rows(damaged_table):
    with closing(_source_db("fresh")) as db:
        _seed_complete_source(db)
        source = proof._business_source_image(db)
        db.execute("begin immediate")
        migrate_business_schema(db)
        db.commit()
        if damaged_table == "parent":
            db.execute("update business set title='changed' "
                       "where record_type='task' and public_id=-11")
        else:
            db.execute("update business_task_evidence set evidence_role='completion' "
                       "where rowid=81")
        with pytest.raises(AssertionError):
            proof._assert_original_business_rows_preserved(
                source, proof._business_target_image(db, "fresh")
            )


def test_business_image_summary_exposes_counts_and_digest_only():
    with closing(_source_db("fresh")) as db:
        _seed_complete_source(db)
        source = proof._business_source_image(db)
        summary = proof._business_image_summary(source)
        assert summary["variant"] == "fresh"
        assert len(summary["parent_counts"]) == 4
        assert len(summary["child_counts"]) == 15
        assert len(summary["facts_sha256"]) == 64
        assert "rows" not in str(summary)
        db.execute("update business_tasks set title='changed' where id=-11")
        assert proof._business_image_summary(proof._business_source_image(db))[
            "facts_sha256"
        ] != summary["facts_sha256"]
