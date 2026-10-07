from __future__ import annotations

import sqlite3

from app import store as store_module
from app.store import AutoReplyStore


def _project(store: AutoReplyStore) -> int:
    with store._connect() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="project",
            anchor_ref="registry:crm-customer-migration",
            title="甲客户一期交付项目",
            _db=db,
        )
        return store.create_business_project_in_transaction(
            canonical_anchor_id=anchor_id,
            title="甲客户一期交付项目",
            registry_source="management_weekly_report:source:1",
            _db=db,
        )


def test_existing_project_schema_migration_keeps_identity_and_leaves_customer_null(tmp_path):
    path = tmp_path / "projects.sqlite3"
    store = AutoReplyStore(path)
    project_id = _project(store)

    with sqlite3.connect(path) as db:
        columns = {
            row[1] for row in db.execute("pragma table_info(business_projects)")
        }
        crm_columns = {
            "crm_customer_id",
            "crm_customer_name",
            "crm_customer_lookup_status",
            "crm_customer_candidates_json",
            "crm_customer_label",
            "crm_customer_evidence_json",
        }
        for column in crm_columns & columns:
            db.execute(f"alter table business_projects drop column {column}")
        db.execute(
            "update business_projects set title=? where id=?",
            ("甲客户一期交付项目", project_id),
        )

    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    migrated = AutoReplyStore(path).get_business_project(project_id)

    assert migrated is not None
    assert migrated.title == "甲客户一期交付项目"
    assert migrated.crm_customer_id == ""
    assert migrated.crm_customer_name == ""
    assert migrated.crm_customer_lookup_status == "not_requested"
    assert migrated.crm_customer_candidates == []
    with sqlite3.connect(path) as db:
        migrated_columns = {
            row[1] for row in db.execute("pragma table_info(business_projects)")
        }
    assert crm_columns.issubset(migrated_columns)


def test_project_customer_lookup_preserves_confirmed_association_on_failure(tmp_path):
    store = AutoReplyStore(tmp_path / "projects.sqlite3")
    project_id = _project(store)
    project = store.get_business_project(project_id)
    assert project is not None
    anchor_id = project.canonical_anchor_id

    # The store update API is expected to preserve the selected CRM identity
    # while replacing only the latest lookup result.
    with store._connect() as db:
        store.update_business_project_crm_customer_lookup_in_transaction(
            project_id=project_id,
            label="甲客户",
            evidence=None,
            lookup_status="matched",
            candidates=[],
            matched_customer_id="crm-123",
            matched_customer_name="甲客户",
            _db=db,
        )
        store.update_business_project_crm_customer_lookup_in_transaction(
            project_id=project_id,
            label="甲客户",
            evidence=None,
            lookup_status="unavailable",
            candidates=[],
            _db=db,
        )

    updated = store.get_business_project(project_id)
    assert updated is not None
    assert updated.canonical_anchor_id == anchor_id
    assert updated.title == "甲客户一期交付项目"
    assert updated.crm_customer_id == "crm-123"
    assert updated.crm_customer_name == "甲客户"
    assert updated.crm_customer_lookup_status == "unavailable"


def test_conflicting_later_match_does_not_replace_confirmed_crm_customer(tmp_path):
    store = AutoReplyStore(tmp_path / "projects.sqlite3")
    project_id = _project(store)
    with store._connect() as db:
        store.update_business_project_crm_customer_lookup_in_transaction(
            project_id=project_id,
            label="甲客户",
            evidence=None,
            lookup_status="matched",
            candidates=[],
            matched_customer_id="crm-current",
            matched_customer_name="已关联客户",
            _db=db,
        )
        store.update_business_project_crm_customer_lookup_in_transaction(
            project_id=project_id,
            label="乙客户",
            evidence=None,
            lookup_status="matched",
            candidates=[{"customer_id": "crm-new", "name": "新候选", "matched_fields": ["name"]}],
            matched_customer_id="crm-new",
            matched_customer_name="新候选",
            _db=db,
        )

    updated = store.get_business_project(project_id)
    assert updated is not None
    assert updated.crm_customer_id == "crm-current"
    assert updated.crm_customer_name == "已关联客户"
    assert updated.crm_customer_lookup_status == "conflict"
    assert [candidate.customer_id for candidate in updated.crm_customer_candidates] == ["crm-new"]


def test_explicit_clear_removes_only_the_local_project_customer_link(tmp_path):
    store = AutoReplyStore(tmp_path / "projects.sqlite3")
    project_id = _project(store)
    with store._connect() as db:
        store.update_business_project_crm_customer_lookup_in_transaction(
            project_id=project_id,
            label="甲客户",
            evidence=None,
            lookup_status="matched",
            candidates=[],
            matched_customer_id="crm-current",
            matched_customer_name="甲客户",
            _db=db,
        )

    assert store.clear_business_project_crm_customer(project_id=project_id)
    project = store.get_business_project(project_id)
    assert project is not None
    assert project.title == "甲客户一期交付项目"
    assert project.crm_customer_id == ""
    assert project.crm_customer_lookup_status == "not_requested"


def test_project_crm_confirmation_accepts_only_saved_candidate_and_is_idempotent(tmp_path):
    store = AutoReplyStore(tmp_path / "projects.sqlite3")
    project_id = _project(store)
    candidate = {
        "customer_id": "crm-123",
        "name": "甲客户",
        "alias": "甲客",
        "registered_name": "甲客户科技有限公司",
        "matched_fields": ["name"],
    }
    with store._connect() as db:
        store.update_business_project_crm_customer_lookup_in_transaction(
            project_id=project_id,
            label="甲客户",
            evidence=None,
            lookup_status="ambiguous",
            candidates=[candidate],
            _db=db,
        )

    assert store.confirm_business_project_crm_customer(
        project_id=project_id, customer_id="crm-123"
    )
    assert store.confirm_business_project_crm_customer(
        project_id=project_id, customer_id="crm-123"
    )
    updated = store.get_business_project(project_id)
    assert updated is not None
    assert updated.crm_customer_id == "crm-123"
    assert updated.crm_customer_name == "甲客户"
    assert updated.crm_customer_lookup_status == "matched"
    assert updated.crm_customer_candidates == []

    assert not store.confirm_business_project_crm_customer(
        project_id=project_id, customer_id="arbitrary-unseen-id"
    )
