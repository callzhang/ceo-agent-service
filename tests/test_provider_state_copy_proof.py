"""Provider source-shaped copy evidence on literal actual and fresh DDL."""

import pytest

from app.provider_state_schema_release import (
    apply_provider_state_migration, rollback_provider_state_migration,
)
from scripts import validate_action_email_copy as proof
from tests.test_provider_state_schema_release import _seed
from tests.test_provider_state_source import OBS, GEN, T2, source_db


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_provider_image_preserves_values_types_and_current_facts(variant):
    with source_db(variant) as db:
        _seed(db)
        db.commit()
        source = proof._provider_source_image(db)
        assert source["variant"] == variant
        assert set(source["facts"]) == {OBS, GEN}
        assert len(source["facts"][OBS][0]) == 9
        assert len(source["facts"][GEN][0]) == 3
        assert sum(len(rows) for _, rows in source["facts"].values()) == 6
        db.execute("begin immediate")
        apply_provider_state_migration(db, {
            "variant": source["variant"], "tables": source["declarations"],
        })
        initial_target = proof._provider_target_image(db, variant)
        proof._assert_provider_facts_equal(source, initial_target)
        db.commit()

        db.execute("begin immediate")
        db.execute(
            "update provider_state set observed_at=? where record_type='observation' "
            "and account_id='account-a' and stable_message_identity='message-a'", (T2,),
        )
        db.execute(
            "insert into provider_state(record_type,account_id,provider_folder_id,"
            "observed_at,payload_json) values('folder_generation','account-c','INBOX',?,'{}')",
            (T2,),
        )
        db.commit()
        current = proof._provider_target_image(db, variant)
        assert current != initial_target

        db.execute("begin immediate")
        rollback_provider_state_migration(db, {
            "variant": source["variant"], "tables": source["declarations"],
        })
        restored = proof._provider_source_image(db)
        proof._assert_provider_facts_equal(current, restored)
        assert restored["declarations"] == source["declarations"]
        db.commit()

        db.execute("begin immediate")
        apply_provider_state_migration(db, {
            "variant": source["variant"], "tables": source["declarations"],
        })
        proof._assert_provider_facts_equal(restored, proof._provider_target_image(db, variant))
        db.rollback()


@pytest.mark.parametrize("field", ("state", "provider_folder_name", "observed_at"))
def test_provider_image_rejects_changed_original_field(field):
    with source_db("fresh") as db:
        _seed(db)
        db.commit()
        original = proof._provider_source_image(db)
        db.execute("begin immediate")
        apply_provider_state_migration(db, {
            "variant": original["variant"], "tables": original["declarations"],
        })
        db.commit()
        updates = {
            "state": "state='excluded'",
            "provider_folder_name": "payload_json=json_set(payload_json,'$.provider_folder_name','changed')",
            "observed_at": "observed_at='changed'",
        }
        db.execute(
            f"update provider_state set {updates[field]} "
            "where record_type='observation' and account_id='account-a' "
            "and stable_message_identity='message-a'"
        )
        with pytest.raises(AssertionError):
            proof._assert_original_provider_rows_preserved(
                original, proof._provider_target_image(db, "fresh")
            )


def test_provider_summary_has_derived_counts_and_digest_only():
    with source_db("fresh") as db:
        _seed(db)
        image = proof._provider_source_image(db)
        summary = proof._provider_image_summary(image)
        assert summary["counts"] == {OBS: 3, GEN: 3}
        assert len(summary["facts_sha256"]) == 64
        assert len(summary["declarations_sha256"]) == 64
        assert "message-a" not in str(summary)
        db.execute(f"update {GEN} set observed_at=? where account_id='account-a' "
                   "and provider_folder_id='inbox'", (T2,))
        assert proof._provider_image_summary(proof._provider_source_image(db))[
            "facts_sha256"
        ] != summary["facts_sha256"]
