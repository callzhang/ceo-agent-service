from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import app.store as store_module
from app.managed_skills import ManagedSkillValidationError
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
