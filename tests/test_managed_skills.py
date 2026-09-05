from __future__ import annotations

import hashlib
import os
import sqlite3
from threading import Barrier, Thread
from pathlib import Path

import pytest

import app.store as store_module
from app.business_skills import BUNDLED_BUSINESS_SKILL_NAMES, load_bundled_business_skills
from app.managed_skills import (
    ManagedSkillValidationError,
    REPOSITORY_IMPORT_SOURCE,
    import_repository_managed_skills,
    resolve_pending_runtime_skills,
)
from app.store import AutoReplyStore


SKILL_V1 = """---
name: ceo-test
description: Test managed Skill
metadata:
  managed_by: ceo-agent-service
---

# Version one
"""

SKILL_V2 = """---
name: ceo-test
description: Test managed Skill
metadata:
  managed_by: ceo-agent-service
---

# Version two
"""


def _copy_existing_feedback_schema_without_managed_skill_tables(path: Path) -> None:
    """Build a pre-managed-Skill database containing a persisted feedback item.

    The fixture begins from the repository's existing feedback schema, then
    removes only the tables introduced by the managed-Skill migration.  This
    keeps the fixture aligned with the real legacy feedback projection while
    exercising the additive upgrade path rather than an empty database path.
    """
    legacy = AutoReplyStore(path)
    legacy.upsert_feedback_event(
        key="manual:8308",
        feedback_token="manual-attempt:8308",
        comment="existing feedback must survive the managed-Skill migration",
        source="workbench",
    )
    with legacy._connect() as db:
        for table in (
            "runtime_feedback_iteration_capabilities",
            "runtime_skill_load_receipts",
            "runtime_skill_bindings",
            "runtime_skill_configs",
            "managed_skill_revisions",
            "managed_skills",
        ):
            db.execute(f"drop table {table}")
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())


def test_managed_skill_migration_is_additive_for_existing_feedback_database(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "existing-feedback.sqlite3"
    _copy_existing_feedback_schema_without_managed_skill_tables(db_path)

    migrated = AutoReplyStore(db_path)

    assert migrated.get_feedback_processing_item("manual:8308") is not None
    assert migrated.list_managed_skills() == ()

    snapshot = resolve_pending_runtime_skills(migrated, pid=os.getpid())
    active = migrated.get_active_runtime_skill_config()

    assert snapshot.revisions
    assert active is not None
    assert active.id == snapshot.config_id
    assert {
        skill.name for skill in migrated.list_managed_skills()
    } == set(BUNDLED_BUSINESS_SKILL_NAMES)


def test_initial_import_creates_revisions_and_initial_config_for_service_owned_skills(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "import.sqlite3")
    imported = import_repository_managed_skills(store)

    assert {entry.name for entry in imported} == set(BUNDLED_BUSINESS_SKILL_NAMES)
    assert {entry.revision_number for entry in imported} == {1}
    assert {entry.source for entry in imported} == {"repository:skills"}
    config = store.get_pending_or_active_runtime_skill_config()
    assert config is not None
    assert config.status == "pending_restart"
    assert {binding.skill_id for binding in store.list_runtime_skill_bindings(config.id)} == {
        skill.id for skill in store.list_managed_skills()
    }


def test_repository_import_is_idempotent_and_preserves_user_owned_name(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "import.sqlite3")
    user = store.create_managed_skill("ceo-message-triage", "My custom triage")
    user_revision = store.create_managed_skill_revision(
        user.id, SKILL_V1.replace("ceo-test", "ceo-message-triage"), source="settings"
    )

    first = import_repository_managed_skills(store)
    second = import_repository_managed_skills(store)

    assert "ceo-message-triage" not in {entry.name for entry in first}
    assert second == ()
    assert store.get_managed_skill(user.id) == user
    assert store.list_managed_skill_revisions(user.id) == (user_revision,)


def test_repository_import_reconciles_partial_service_owned_records_into_initial_config(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "partial.sqlite3")
    bundled = load_bundled_business_skills()[0]
    existing = store.create_managed_skill(bundled.name, bundled.name)
    first = store.create_managed_skill_revision(
        existing.id, bundled.content, source=REPOSITORY_IMPORT_SOURCE
    )
    latest = store.create_managed_skill_revision(
        existing.id,
        bundled.content.replace("description:", "description: Updated ", 1),
        source="settings",
    )

    import_repository_managed_skills(store)

    config = store.get_pending_or_active_runtime_skill_config()
    assert config is not None
    bindings = store.list_runtime_skill_bindings(config.id)
    revisions_by_skill = {binding.skill_id: binding.revision_id for binding in bindings}
    assert revisions_by_skill[existing.id] == latest.id
    assert first.id != latest.id
    assert {
        store.get_managed_skill(binding.skill_id).name for binding in bindings
    } == set(BUNDLED_BUSINESS_SKILL_NAMES)


def test_repository_import_preserves_an_existing_runtime_config(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "configured.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")
    existing_config = store.create_runtime_skill_config(
        {skill.id: revision.id}, expected_parent_id=None
    )

    import_repository_managed_skills(store)

    assert store.get_pending_or_active_runtime_skill_config() == existing_config
    assert store.list_runtime_skill_bindings(existing_config.id)[0].revision_id == revision.id


def test_repository_import_serializes_two_independent_store_initializers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-import.sqlite3"
    stores = (AutoReplyStore(path), AutoReplyStore(path))
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def initialize(store: AutoReplyStore) -> None:
        try:
            barrier.wait()
            import_repository_managed_skills(store)
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=initialize, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    final = AutoReplyStore(path)
    assert {skill.name for skill in final.list_managed_skills()} == set(
        BUNDLED_BUSINESS_SKILL_NAMES
    )
    config = final.get_pending_or_active_runtime_skill_config()
    assert config is not None
    assert config.status == "pending_restart"
    assert {
        final.get_managed_skill(binding.skill_id).name
        for binding in final.list_runtime_skill_bindings(config.id)
    } == set(BUNDLED_BUSINESS_SKILL_NAMES)
    with final._connect() as db:
        assert db.execute("select count(*) from runtime_skill_configs").fetchone()[0] == 1


def test_create_revision_keeps_prior_body_and_hash(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")

    first = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")
    second = store.create_managed_skill_revision(skill.id, SKILL_V2, source="settings")

    assert first.id != second.id
    assert first.revision_number == 1
    assert second.revision_number == 2
    assert second.parent_revision_id == first.id
    assert store.get_managed_skill_revision(first.id).content == SKILL_V1
    assert first.sha256 == hashlib.sha256(SKILL_V1.encode("utf-8")).hexdigest()
    assert first.sha256 != second.sha256


def test_invalid_frontmatter_creates_no_revision(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")

    with pytest.raises(ManagedSkillValidationError, match="managed_by"):
        store.create_managed_skill_revision(
            skill.id,
            "---\nname: ceo-test\ndescription: Missing marker\n---\n",
            source="settings",
        )

    assert store.list_managed_skill_revisions(skill.id) == ()


def test_duplicate_skill_name_is_rejected(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    store.create_managed_skill("ceo-test", "Test Skill")

    with pytest.raises(ValueError, match="managed Skill already exists"):
        store.create_managed_skill("ceo-test", "Other title")


def test_duplicate_revision_content_is_rejected_without_new_revision(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    first = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")

    with pytest.raises(ValueError, match="managed Skill revision content already exists"):
        store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")

    assert store.list_managed_skill_revisions(skill.id) == (first,)


def test_revision_number_is_unique_within_a_managed_skill(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    first = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")

    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            insert into managed_skill_revisions (
                skill_id, revision_number, content, sha256, source
            ) values (?, ?, ?, ?, ?)
            """,
            (skill.id, first.revision_number, SKILL_V2, "other-hash", "test"),
        )


def test_parent_revision_must_belong_to_the_same_skill(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    first_skill = store.create_managed_skill("ceo-test", "Test Skill")
    other_skill = store.create_managed_skill("ceo-other", "Other Skill")
    parent = store.create_managed_skill_revision(first_skill.id, SKILL_V1, source="settings")
    other_content = SKILL_V2.replace("ceo-test", "ceo-other")

    with pytest.raises(ValueError, match="parent revision does not belong to managed Skill"):
        store.create_managed_skill_revision(
            other_skill.id,
            other_content,
            source="settings",
            parent_revision_id=parent.id,
        )

    assert store.list_managed_skill_revisions(other_skill.id) == ()


def test_schema_initialization_is_idempotent_and_has_revision_indexes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "skills.sqlite3"
    AutoReplyStore(path)
    AutoReplyStore(path)

    with sqlite3.connect(path) as db:
        indexes = {
            row[1]
            for row in db.execute("pragma index_list(managed_skill_revisions)")
        }

    assert {"idx_managed_skill_revisions_number", "idx_managed_skill_revisions_sha256"} <= indexes


def test_revision_rows_reject_sql_update_and_delete_without_data_loss(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")

    with store._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="managed Skill revisions are immutable"
    ):
        db.execute(
            "update managed_skill_revisions set content=? where id=?",
            (SKILL_V2, revision.id),
        )
    with store._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="managed Skill revisions are immutable"
    ):
        db.execute("delete from managed_skill_revisions where id=?", (revision.id,))

    persisted = store.get_managed_skill_revision(revision.id)
    assert persisted is not None
    assert persisted.content == SKILL_V1
    assert persisted.sha256 == revision.sha256


def test_padded_skill_names_are_normalized_before_persistence(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")

    skill = store.create_managed_skill("  ceo-test  ", "  Test Skill  ")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")

    assert skill.name == "ceo-test"
    assert skill.display_name == "Test Skill"
    assert revision.skill_id == skill.id


def test_reopening_an_existing_database_repairs_missing_immutability_triggers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "skills.sqlite3"
    store = AutoReplyStore(path)
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")
    with store._connect() as db:
        db.execute("drop trigger trg_managed_skill_revisions_immutable_update")
        db.execute("drop trigger trg_managed_skill_revisions_immutable_delete")
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())

    reopened = AutoReplyStore(path)

    with reopened._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="managed Skill revisions are immutable"
    ):
        db.execute(
            "update managed_skill_revisions set content=? where id=?",
            (SKILL_V2, revision.id),
        )
