from __future__ import annotations

import hashlib
import os
import sqlite3
from threading import Barrier, Thread
from pathlib import Path

import pytest

import app.managed_skills as managed_skills_module
import app.store as store_module
from app.business_skills import load_bundled_business_skills
from app.managed_skills import (
    ManagedSkillValidationError,
    REPOSITORY_IMPORT_SOURCE,
    REPOSITORY_MANAGED_SKILL_NAMES,
    export_managed_skill_revision,
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
    } == set(REPOSITORY_MANAGED_SKILL_NAMES)


def test_initial_import_creates_revisions_and_initial_config_for_service_owned_skills(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "import.sqlite3")
    imported = import_repository_managed_skills(store)

    expected_names = set(REPOSITORY_MANAGED_SKILL_NAMES)
    assert {entry.name for entry in imported} == expected_names
    assert {entry.revision_number for entry in imported} == {1}
    assert {entry.source for entry in imported} == {"repository:skills"}
    config = store.get_pending_or_active_runtime_skill_config()
    assert config is not None
    assert config.status == "pending_restart"
    assert {binding.skill_id for binding in store.list_runtime_skill_bindings(config.id)} == {
        skill.id for skill in store.list_managed_skills()
    }
    feedback_skill = store.get_managed_skill_by_name("ceo-feedback-iteration")
    assert feedback_skill is not None
    assert next(
        binding for binding in store.list_runtime_skill_bindings(config.id)
        if binding.skill_id == feedback_skill.id
    ).purpose == "feedback_iteration"
    wechat_skill = store.get_managed_skill_by_name("ceo-wechat")
    assert wechat_skill is not None
    assert "# CEO WeChat" in store.list_managed_skill_revisions(wechat_skill.id)[0].content


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


def test_minutes_sync_repository_import_preserves_exact_bytes_and_revision(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "minutes-sync.sqlite3")
    expected = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "ceo-minutes-sync"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    import_repository_managed_skills(store)

    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    revision = store.list_managed_skill_revisions(skill.id)[0]
    assert revision.content == expected
    assert revision.sha256 == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert revision.source == REPOSITORY_IMPORT_SOURCE
    assert revision.parent_revision_id is None


def test_minutes_sync_repository_import_does_not_overwrite_custom_same_name(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "custom-minutes-sync.sqlite3")
    custom = store.create_managed_skill("ceo-minutes-sync", "Custom minutes sync")
    custom_revision = store.create_managed_skill_revision(
        custom.id,
        SKILL_V1.replace("ceo-test", "ceo-minutes-sync"),
        source="settings",
    )

    imported = import_repository_managed_skills(store)

    assert "ceo-minutes-sync" not in {entry.name for entry in imported}
    assert store.list_managed_skill_revisions(custom.id) == (custom_revision,)


def test_repository_import_appends_changed_exact_bytes_to_pure_repository_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "changed-repository-skill.sqlite3")
    first_content = dict(managed_skills_module._repository_managed_skills())[
        "ceo-minutes-sync"
    ]
    monkeypatch.setattr(
        managed_skills_module,
        "_repository_managed_skills",
        lambda: (("ceo-minutes-sync", first_content),),
    )
    first = import_repository_managed_skills(store)[0]
    changed_content = first_content.replace(
        "# CEO Minutes Sync", "# CEO Minutes Sync\n\nRepository revision two", 1
    )
    monkeypatch.setattr(
        managed_skills_module,
        "_repository_managed_skills",
        lambda: (("ceo-minutes-sync", changed_content),),
    )

    changed = import_repository_managed_skills(store)
    unchanged = import_repository_managed_skills(store)

    assert len(changed) == 1
    assert changed[0].revision_number == 2
    assert changed[0].sha256 == hashlib.sha256(changed_content.encode()).hexdigest()
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    revisions = store.list_managed_skill_revisions(skill.id)
    assert revisions[1].parent_revision_id == first.revision_id
    assert revisions[1].content == changed_content
    assert unchanged == ()
    assert len(revisions) == 2


def test_concurrent_changed_repository_import_appends_only_one_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "concurrent-changed-import.sqlite3"
    store = AutoReplyStore(path)
    import_repository_managed_skills(store)
    current_content = dict(managed_skills_module._repository_managed_skills())[
        "ceo-minutes-sync"
    ]
    changed_content = current_content.replace(
        "# CEO Minutes Sync", "# CEO Minutes Sync\n\nConcurrent revision", 1
    )
    original = managed_skills_module._repository_managed_skills
    monkeypatch.setattr(
        managed_skills_module,
        "_repository_managed_skills",
        lambda: tuple(
            (name, changed_content if name == "ceo-minutes-sync" else content)
            for name, content in original()
        ),
    )
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def reconcile() -> None:
        try:
            barrier.wait()
            import_repository_managed_skills(AutoReplyStore(path))
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=reconcile) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    skill = store.get_managed_skill_by_name("ceo-minutes-sync")
    assert skill is not None
    revisions = store.list_managed_skill_revisions(skill.id)
    assert sum(revision.content == changed_content for revision in revisions) == 1


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
    } == set(REPOSITORY_MANAGED_SKILL_NAMES)


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
        REPOSITORY_MANAGED_SKILL_NAMES
    )
    config = final.get_pending_or_active_runtime_skill_config()
    assert config is not None
    assert config.status == "pending_restart"
    assert {
        final.get_managed_skill(binding.skill_id).name
        for binding in final.list_runtime_skill_bindings(config.id)
    } == set(REPOSITORY_MANAGED_SKILL_NAMES)
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


def test_explicit_repository_export_writes_only_the_selected_immutable_revision(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")

    exported = export_managed_skill_revision(store, revision.id, skills_root=tmp_path / "skills")

    assert exported.name == "ceo-test"
    assert exported.revision_id == revision.id
    assert (tmp_path / "skills" / "ceo-test" / "SKILL.md").read_text(encoding="utf-8") == SKILL_V1


def test_export_rejects_a_symlinked_destination_directory_without_writing_outside(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")
    skills_root = tmp_path / "skills"
    outside = tmp_path / "outside"
    outside.mkdir()
    skills_root.mkdir()
    (skills_root / skill.name).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ManagedSkillValidationError, match="symlink"):
        export_managed_skill_revision(store, revision.id, skills_root=skills_root)

    assert not (outside / "SKILL.md").exists()


@pytest.mark.parametrize(
    "name",
    ("../ceo-test", "ceo/test", r"ceo\\test", "ceo.test", "ceo\x1ftest"),
)
def test_store_rejects_unsafe_managed_skill_names_before_persistence(
    tmp_path: Path, name: str
) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")

    with pytest.raises(ManagedSkillValidationError, match="invalid managed Skill name"):
        store.create_managed_skill(name, "Test Skill")

    assert store.list_managed_skills() == ()


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


def test_padded_skill_names_are_rejected_before_persistence(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")

    with pytest.raises(ManagedSkillValidationError, match="invalid managed Skill name"):
        store.create_managed_skill("  ceo-test  ", "Test Skill")

    assert store.list_managed_skills() == ()


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
