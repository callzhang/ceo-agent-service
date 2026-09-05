from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import app.store as store_module
from app.managed_skills import resolve_pending_runtime_skills
from app.store import AutoReplyStore
from app.consumer_agent import ConsumerAgentRunner, consumer_wire_contract_hash


SKILL_V1 = """---
name: ceo-test
description: Test managed Skill
metadata:
  managed_by: ceo-agent-service
---

# Version one
"""

SKILL_V2 = SKILL_V1.replace("Version one", "Version two")


def configured_store(tmp_path: Path) -> tuple[AutoReplyStore, object]:
    store = AutoReplyStore(tmp_path / "skills.sqlite3")
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    return store, store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")


def test_pending_config_becomes_active_only_after_matching_load_receipt(tmp_path: Path) -> None:
    store, revision = configured_store(tmp_path)
    pending = store.create_runtime_skill_config(
        {revision.skill_id: revision.id}, expected_parent_id=None
    )

    assert pending.status == "pending_restart"
    assert store.get_active_runtime_skill_config() is None
    receipt = store.record_runtime_skill_load(
        pending.id, pid=7654, loaded={revision.skill_id: revision.sha256}
    )
    assert receipt.config_id == pending.id
    assert store.get_active_runtime_skill_config().id == pending.id


def test_failed_load_keeps_previous_active_config(tmp_path: Path) -> None:
    store, first = configured_store(tmp_path)
    active = store.create_runtime_skill_config(
        {first.skill_id: first.id}, expected_parent_id=None
    )
    store.record_runtime_skill_load(active.id, pid=7654, loaded={first.skill_id: first.sha256})
    second = store.create_managed_skill_revision(first.skill_id, SKILL_V2, source="settings")
    candidate = store.create_runtime_skill_config(
        {second.skill_id: second.id}, expected_parent_id=active.id
    )

    store.record_runtime_skill_load_failure(candidate.id, pid=7655, error="revision rejected")

    assert store.get_runtime_skill_config(candidate.id).status == "load_failed"
    assert store.get_active_runtime_skill_config().id == active.id

    rollback = store.create_runtime_skill_rollback_config(
        active.id, expected_parent_id=candidate.id
    )
    assert rollback.id != active.id
    assert rollback.status == "pending_restart"
    assert [
        (item.skill_id, item.revision_id, item.enabled, item.load_order, item.purpose)
        for item in store.list_runtime_skill_bindings(rollback.id)
    ] == [
        (item.skill_id, item.revision_id, item.enabled, item.load_order, item.purpose)
        for item in store.list_runtime_skill_bindings(active.id)
    ]


def test_failed_latest_candidate_never_revives_an_older_pending_config(
    tmp_path: Path,
) -> None:
    store, first = configured_store(tmp_path)
    active = store.create_runtime_skill_config(
        {first.skill_id: first.id}, expected_parent_id=None
    )
    resolve_pending_runtime_skills(store, pid=7654)
    second = store.create_managed_skill_revision(first.skill_id, SKILL_V2, source="settings")
    pending = store.create_runtime_skill_config(
        {second.skill_id: second.id}, expected_parent_id=active.id
    )
    third = store.create_managed_skill_revision(
        first.skill_id, SKILL_V2.replace("Version two", "Version three"), source="settings"
    )
    failed = store.create_runtime_skill_config(
        {third.skill_id: third.id}, expected_parent_id=pending.id
    )
    store.record_runtime_skill_load_failure(failed.id, pid=7655, error="candidate rejected")

    snapshot = resolve_pending_runtime_skills(store, pid=7656)

    assert snapshot.config_id == active.id
    assert store.get_active_runtime_skill_config().id == active.id
    assert store.get_runtime_skill_config(pending.id).status == "pending_restart"
    with store._connect() as db:
        pending_receipts = db.execute(
            "select count(*) from runtime_skill_load_receipts where config_id=?",
            (pending.id,),
        ).fetchone()[0]
    assert pending_receipts == 0


def test_parent_compare_and_swap_rejects_stale_configuration(tmp_path: Path) -> None:
    store, revision = configured_store(tmp_path)
    first = store.create_runtime_skill_config(
        {revision.skill_id: revision.id}, expected_parent_id=None
    )

    with pytest.raises(ValueError, match="runtime Skill configuration parent conflict"):
        store.create_runtime_skill_config(
            {revision.skill_id: revision.id}, expected_parent_id=None
        )

    assert store.get_runtime_skill_config(first.id) is not None


@pytest.mark.parametrize("bindings", ({1: 999}, {1: "bad"}, {"bad": 1}))
def test_config_rejects_missing_or_malformed_revision_bindings(tmp_path: Path, bindings: object) -> None:
    store, _revision = configured_store(tmp_path)

    with pytest.raises(ValueError):
        store.create_runtime_skill_config(bindings, expected_parent_id=None)


def test_load_receipt_mismatch_is_rejected_without_activation(tmp_path: Path) -> None:
    store, revision = configured_store(tmp_path)
    pending = store.create_runtime_skill_config(
        {revision.skill_id: revision.id}, expected_parent_id=None
    )

    with pytest.raises(ValueError, match="runtime Skill load receipt does not match bindings"):
        store.record_runtime_skill_load(
            pending.id, pid=7654, loaded={revision.skill_id: "not-the-revision-sha"}
        )

    assert store.get_runtime_skill_config(pending.id).status == "pending_restart"


def test_direct_status_transition_requires_a_persisted_load_receipt(tmp_path: Path) -> None:
    store, revision = configured_store(tmp_path)
    pending = store.create_runtime_skill_config(
        {revision.skill_id: revision.id}, expected_parent_id=None
    )

    with store._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="runtime Skill activation requires matching load receipt"
    ):
        db.execute(
            "update runtime_skill_configs set status='active' where id=?", (pending.id,)
        )
    with store._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="runtime Skill activation requires matching load receipt"
    ):
        db.execute(
            "update runtime_skill_configs set status='load_failed' where id=?",
            (pending.id,),
        )

    assert store.get_runtime_skill_config(pending.id).status == "pending_restart"
    store.record_runtime_skill_load(
        pending.id, pid=7654, loaded={revision.skill_id: revision.sha256}
    )
    assert store.get_runtime_skill_config(pending.id).status == "active"


def test_resolver_marks_malformed_candidate_failed_and_returns_prior_active(
    tmp_path: Path,
) -> None:
    store, revision = configured_store(tmp_path)
    active = store.create_runtime_skill_config(
        {revision.skill_id: revision.id}, expected_parent_id=None
    )
    active_snapshot = resolve_pending_runtime_skills(store, pid=7654)
    with sqlite3.connect(store.path) as db:
        candidate_id = int(
            db.execute(
                "insert into runtime_skill_configs (parent_id, status) values (?, 'pending_restart')",
                (active.id,),
            ).lastrowid
        )
        db.execute(
            """insert into runtime_skill_bindings
               (config_id, skill_id, revision_id, enabled, load_order, purpose)
               values (?, ?, ?, 1, 0, '')""",
            (candidate_id, revision.skill_id, revision.id + 999),
        )

    fallback = resolve_pending_runtime_skills(store, pid=7655)

    assert fallback == active_snapshot
    assert store.get_runtime_skill_config(candidate_id).status == "load_failed"


def test_snapshot_remains_exact_when_a_later_config_is_created(tmp_path: Path) -> None:
    store, first = configured_store(tmp_path)
    active = store.create_runtime_skill_config(
        {first.skill_id: first.id}, expected_parent_id=None
    )
    snapshot = resolve_pending_runtime_skills(store, pid=7654)
    second = store.create_managed_skill_revision(first.skill_id, SKILL_V2, source="settings")
    store.create_runtime_skill_config(
        {second.skill_id: second.id}, expected_parent_id=active.id
    )

    assert snapshot.config_id == active.id
    assert tuple(item.content for item in snapshot.revisions) == (SKILL_V1,)
    assert snapshot.protocol().count("Version one") == 1
    assert "Version two" not in snapshot.protocol()


def test_consumer_uses_startup_snapshot_without_reading_installed_business_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, first = configured_store(tmp_path)
    active = store.create_runtime_skill_config(
        {first.skill_id: first.id}, expected_parent_id=None
    )
    snapshot = resolve_pending_runtime_skills(store, pid=7654)
    consumer = ConsumerAgentRunner(
        store=store, workspace=tmp_path, runtime_skill_snapshot=snapshot
    )
    def installed_catalog_must_not_be_read():
        raise AssertionError("managed runtime invocation read mutable installed catalog")

    monkeypatch.setattr(
        "app.consumer_agent.installed_business_skill_catalog",
        installed_catalog_must_not_be_read,
    )
    original_contract = consumer_wire_contract_hash(consumer.runtime_skill_snapshot)
    second = store.create_managed_skill_revision(first.skill_id, SKILL_V2, source="settings")
    store.create_runtime_skill_config(
        {second.skill_id: second.id}, expected_parent_id=active.id
    )

    assert consumer_wire_contract_hash(consumer.runtime_skill_snapshot) == original_contract
    assert "Version one" in consumer.runtime_skill_snapshot.protocol()
    assert "Version two" not in consumer.runtime_skill_snapshot.protocol()


def test_runtime_snapshot_excludes_disabled_managed_bindings(tmp_path: Path) -> None:
    store, enabled = configured_store(tmp_path)
    disabled_skill = store.create_managed_skill("ceo-disabled", "Disabled Skill")
    disabled = store.create_managed_skill_revision(
        disabled_skill.id, SKILL_V1.replace("ceo-test", "ceo-disabled"), source="settings"
    )
    config = store.create_runtime_skill_config(
        [
            {"skill_id": enabled.skill_id, "revision_id": enabled.id, "enabled": True, "load_order": 0},
            {"skill_id": disabled.skill_id, "revision_id": disabled.id, "enabled": False, "load_order": 1},
        ],
        expected_parent_id=None,
    )

    snapshot = resolve_pending_runtime_skills(store, pid=7654)

    assert snapshot.config_id == config.id
    assert snapshot.revisions == (enabled,)


def test_schema_initialization_repairs_runtime_guards_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "skills.sqlite3"
    store = AutoReplyStore(path)
    skill = store.create_managed_skill("ceo-test", "Test Skill")
    revision = store.create_managed_skill_revision(skill.id, SKILL_V1, source="settings")
    config = store.create_runtime_skill_config({skill.id: revision.id}, expected_parent_id=None)
    with store._connect() as db:
        db.execute("drop trigger trg_runtime_skill_configs_immutable_update")
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    reopened = AutoReplyStore(path)

    with reopened._connect() as db, pytest.raises(Exception):
        db.execute("update runtime_skill_configs set parent_id=99 where id=?", (config.id,))
