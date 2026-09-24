from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

import app.task_semantic_import as semantic_import
from app.store import AutoReplyStore
from app.task_business_resolution import BusinessResolutionService
from app.task_semantic_import import (
    apply_task_semantic_import_manifest,
    build_task_semantic_import_manifest,
    read_task_semantic_import_manifest,
    write_task_semantic_import_manifest,
)


def _rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute(f"select * from {table} order by id").fetchall()


def _legacy_store(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    project_id = store.create_work_project(title="客户推进")
    todo_id = store.create_work_todo(project_id=project_id, title="准备报价")
    update_id = store.create_work_update(
        project_id=project_id, source_type="reply_attempt", source_ref="msg-1",
        summary="会议讨论了客户推进", changes_json="{}",
    )
    return store, project_id, todo_id, update_id


def _source_backed_todo(store, *, basis, source_type, author_user_id,
                        authorized=True, source_ref="source-1"):
    if basis == "meeting_action_item" and source_ref == "source-1":
        source_ref = "source-1#todos-sha256=abc"
    project_id = store.create_work_project(title="报价相关工作")
    excerpt = "王明负责提交报价"
    evidence = {
        "formal_basis": basis,
        "source_type": source_type,
        "source_ref": source_ref,
        "source_excerpt": excerpt,
        "owner_excerpt": "王明",
        "owner_user_id": "wangming",
        "owner_name": "王明",
        "author_user_id": author_user_id,
        "author_name": "王明" if author_user_id == "wangming" else "指派人",
        "author_kind": "system" if basis == "external_todo" else "human",
        "assigner_is_authorized": authorized,
        "deliverable_is_explicit": True,
        "owner_is_explicit": True,
    }
    update_id = store.create_work_update(
        project_id=project_id, source_type=source_type, source_ref=source_ref,
        summary=excerpt, changes_json=json.dumps({"formal_task_evidence": evidence}),
    )
    store.enqueue_work_summary_input(
        source_type, source_ref, json.dumps({
            "source": {"type": source_type, "ref": source_ref},
            "summary": excerpt,
            "context": {
                "sender": evidence["author_name"],
                "sender_user_id": author_user_id,
                "owner_identity": {"user_id": "wangming", "name": "王明"},
                "assignment_authorized": authorized,
                "external_task_id": "external-1" if basis == "external_todo" else "",
                "source_conversation_kind": "minutes" if basis == "meeting_action_item" else "group",
            },
        }),
    )
    todo_id = store.create_work_todo(
        project_id=project_id, title="提交报价", owner_user_id="wangming",
        owner_name="王明", created_from_update_id=update_id,
        owner_evidence_json=json.dumps({"source_ref": source_ref, "excerpt": "王明"}),
    )
    return project_id, todo_id, update_id


def test_ambiguous_legacy_project_stays_unresolved_and_readable(tmp_path):
    store, project_id, todo_id, update_id = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path, limit=100)

    item = next(item for item in manifest.items if item.legacy_kind == "work_projects")
    assert item.legacy_project_id == project_id
    assert item.legacy_todo_ids == (todo_id,)
    assert item.disposition == "history_only"
    assert item.semantic_task is None
    assert item.official_project_registry_key == ""
    assert {item.legacy_kind for item in manifest.items} == {
        "work_projects", "work_todos", "work_updates"
    }
    assert len(_rows(store.path, "business_legacy_links")) == 0

    result = apply_task_semantic_import_manifest(store.path, manifest)
    assert result.created == 0
    assert result.history_only == 3
    assert len(_rows(store.path, "business_legacy_links")) == 0
    assert len(_rows(store.path, "business_task_signals")) == 0
    assert len(_rows(store.path, "business_tasks")) == 0
    assert store.get_work_project(project_id) is not None
    assert store.get_work_todo(todo_id) is not None
    assert store.list_work_updates(project_id)[0].id == update_id


@pytest.mark.parametrize(
    ("basis", "source_type", "author_user_id"),
    [
        ("explicit_assignment", "reply_attempt", "assigner"),
        ("explicit_commitment", "reply_attempt", "wangming"),
        ("external_todo", "todo_completion_check", "system"),
        ("meeting_action_item", "ai_minutes", "assigner"),
    ],
)
def test_formal_import_requires_exact_source_backed_basis(
    tmp_path, basis, source_type, author_user_id
):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _, todo_id, _ = _source_backed_todo(
        store, basis=basis, source_type=source_type,
        author_user_id=author_user_id,
    )
    manifest = build_task_semantic_import_manifest(store.path)
    item = next(item for item in manifest.items if item.legacy_kind == "work_todos")
    assert item.legacy_row_id == todo_id
    assert item.disposition == "formal_task"
    assert item.semantic_task["formal_basis"] == basis
    expected_ref = "source-1#todos-sha256=abc" if basis == "meeting_action_item" else "source-1"
    assert item.evidence_refs == (f"{source_type}:{expected_ref}",)

    first = apply_task_semantic_import_manifest(store.path, manifest)
    second = apply_task_semantic_import_manifest(store.path, manifest)
    assert first.created == 1
    assert first.history_only == 2
    assert second.created == 0
    assert second.skipped == 1
    assert second.history_only == 2
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        task = db.execute("select * from business_tasks").fetchone()
        link = db.execute(
            "select * from business_legacy_links where work_todo_id=?", (todo_id,)
        ).fetchone()
        assert task["stage"] == "formal"
        assert task["formal_basis"] == basis
        assert link["task_id"] == task["id"]
        assert db.execute(
            "select count(*) from business_task_evidence where task_id=?", (task["id"],)
        ).fetchone()[0] == 1


def test_untrusted_basis_or_owner_does_not_promote(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner", authorized=False,
    )
    manifest = build_task_semantic_import_manifest(store.path)
    item = next(item for item in manifest.items if item.legacy_kind == "work_todos")
    assert item.disposition == "history_only"
    assert item.semantic_task is None


def test_agent_written_evidence_without_source_input_stays_unresolved(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _, todo_id, _ = _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner",
    )
    with sqlite3.connect(store.path) as db:
        db.execute("delete from work_summary_inputs")
    manifest = build_task_semantic_import_manifest(store.path)
    item = next(item for item in manifest.items if item.legacy_kind == "work_todos")
    assert item.legacy_row_id == todo_id
    assert item.disposition == "history_only"


def test_candidate_evidence_is_retained_in_history_not_promoted(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    project_id = store.create_work_project(title="客户推进")
    source_type, source_ref = "reply_attempt", "candidate-msg"
    summary = "可以研究美国报价方案"
    update_id = store.create_work_update(
        project_id=project_id, source_type=source_type, source_ref=source_ref,
        summary=summary, changes_json=json.dumps({
            "candidate_task_evidence": {"source_excerpt": summary}
        }),
    )
    store.enqueue_work_summary_input(source_type, source_ref, json.dumps({
        "source": {"type": source_type, "ref": source_ref}, "summary": summary,
        "context": {"source_conversation_kind": "group"},
    }))
    store.create_work_todo(
        project_id=project_id, title="研究美国报价方案", created_from_update_id=update_id,
    )
    manifest = build_task_semantic_import_manifest(store.path)
    item = next(item for item in manifest.items if item.legacy_kind == "work_todos")
    assert item.disposition == "history_only"
    assert item.semantic_task is None
    result = apply_task_semantic_import_manifest(store.path, manifest)
    assert result.history_only == len(manifest.items)
    with sqlite3.connect(store.path) as db:
        assert db.execute("select count(*) from business_tasks").fetchone()[0] == 0
        assert db.execute("select count(*) from business_task_signals").fetchone()[0] == 0


def test_official_project_requires_exact_pre_registered_registry_key(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="registry:quote", title="正式报价项目"
    )
    official_id = resolution.register_official_project(
        anchor_id=anchor_id, registry_source="registry"
    )
    matched = store.create_work_project(
        title="旧项目名称", memory_context_json=json.dumps(
            {"official_project_registry_key": "registry:quote"}
        ),
    )
    same_title = store.create_work_project(title="正式报价项目")
    manifest = build_task_semantic_import_manifest(store.path)
    by_id = {item.legacy_row_id: item for item in manifest.items}
    assert by_id[matched].disposition == "official_project_match"
    assert by_id[same_title].disposition == "history_only"

    apply_task_semantic_import_manifest(store.path, manifest)
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select project_id from business_legacy_links where work_project_id=?",
            (matched,),
        ).fetchone()[0] == official_id
        assert db.execute("select count(*) from business_projects").fetchone()[0] == 1


def test_changed_source_fingerprint_rejects_before_first_write(tmp_path):
    store, _, todo_id, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    store.update_work_todo(todo_id, title="被更改的任务")
    with pytest.raises(ValueError, match="fingerprint"):
        apply_task_semantic_import_manifest(store.path, manifest)
    assert len(_rows(store.path, "business_legacy_links")) == 0


def test_manifest_tampering_rejects_before_first_write(tmp_path):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    bad_item = replace(manifest.items[0], source_digest="0" * 64)
    bad_manifest = replace(manifest, items=(bad_item, *manifest.items[1:]))
    with pytest.raises(ValueError, match="manifest|digest"):
        apply_task_semantic_import_manifest(store.path, bad_manifest)
    assert len(_rows(store.path, "business_legacy_links")) == 0


def test_limited_apply_reports_history_only_without_creating_links(tmp_path):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    first = apply_task_semantic_import_manifest(store.path, manifest, limit=1)
    assert first.created == 0
    assert first.history_only == 1
    rest = apply_task_semantic_import_manifest(store.path, manifest)
    assert rest.created == 0
    assert rest.history_only == len(manifest.items)
    assert len(_rows(store.path, "business_legacy_links")) == 0


def test_plan_limit_selects_recent_legacy_rows_first(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    old_id = store.create_work_project(title="旧工作")
    new_id = store.create_work_project(title="近期工作")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update work_projects set last_activity_at='2026-01-01 00:00:00' where id=?",
            (old_id,),
        )
        db.execute(
            "update work_projects set last_activity_at='2026-09-24 00:00:00' where id=?",
            (new_id,),
        )
    manifest = build_task_semantic_import_manifest(store.path, limit=1)
    assert manifest.items[0].legacy_row_id == new_id


def test_manifest_for_one_database_rejects_another_copy(tmp_path):
    source = AutoReplyStore(tmp_path / "source.sqlite3")
    source.create_work_project(title="同一工作")
    target = AutoReplyStore(tmp_path / "target.sqlite3")
    target.create_work_project(title="同一工作")
    manifest = build_task_semantic_import_manifest(source.path)
    with pytest.raises(ValueError, match="fingerprint"):
        apply_task_semantic_import_manifest(target.path, manifest)
    assert len(_rows(target.path, "business_legacy_links")) == 0


def test_existing_link_to_different_semantic_object_is_not_idempotent(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _, first_todo_id, _ = _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner", source_ref="source-a",
    )
    _, second_todo_id, _ = _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner", source_ref="source-b",
    )
    manifest = build_task_semantic_import_manifest(store.path)
    apply_task_semantic_import_manifest(store.path, manifest)
    with sqlite3.connect(store.path) as db:
        second_task_id = db.execute(
            "select task_id from business_legacy_links where work_todo_id=?",
            (second_todo_id,),
        ).fetchone()[0]
        db.execute(
            "update business_legacy_links set task_id=? where work_todo_id=?",
            (second_task_id, first_todo_id),
        )
    with pytest.raises(ValueError, match="conflicting legacy link"):
        apply_task_semantic_import_manifest(store.path, manifest)


def test_bounded_apply_fingerprints_corpus_only_once(tmp_path, monkeypatch):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    original = semantic_import._fingerprint
    calls = 0

    def count_fingerprint(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(semantic_import, "_fingerprint", count_fingerprint)
    apply_task_semantic_import_manifest(store.path, manifest)
    assert calls == 1


@pytest.mark.parametrize("changed_source", ["work_updates", "work_summary_inputs"])
def test_per_item_transaction_rechecks_supporting_source_before_write(
    tmp_path, monkeypatch, changed_source
):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner",
    )
    with sqlite3.connect(store.path) as db:
        db.execute("update work_projects set last_activity_at='2026-01-01 00:00:00'")
        db.execute("update work_updates set created_at='2026-01-01 00:00:00'")
        db.execute("update work_todos set updated_at='2026-12-01 00:00:00'")
    manifest = build_task_semantic_import_manifest(store.path)
    assert manifest.items[0].legacy_kind == "work_todos"
    original_check = semantic_import._check_manifest

    def change_source_after_preflight(path, checked_manifest):
        original_check(path, checked_manifest)
        with sqlite3.connect(path) as db:
            if changed_source == "work_updates":
                db.execute("update work_updates set summary='changed' where id=1")
            else:
                db.execute(
                    "update work_summary_inputs set payload_json='{}' where id=1"
                )

    monkeypatch.setattr(semantic_import, "_check_manifest", change_source_after_preflight)
    with pytest.raises(ValueError, match="supporting source digest"):
        apply_task_semantic_import_manifest(store.path, manifest, limit=1)
    assert len(_rows(store.path, "business_legacy_links")) == 0


def test_manifest_roundtrip_and_version_validation(tmp_path):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    path = tmp_path / "manifest.json"
    write_task_semantic_import_manifest(manifest, path)
    assert read_task_semantic_import_manifest(path) == manifest
    with pytest.raises(ValueError, match="version"):
        apply_task_semantic_import_manifest(store.path, replace(manifest, version=999))
    assert len(_rows(store.path, "business_legacy_links")) == 0


def test_plan_reads_pre_cutover_database_without_semantic_tables(tmp_path):
    db_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            create table work_projects (
                id integer primary key, title text, memory_context_json text,
                created_at text
            );
            create table work_todos (
                id integer primary key, project_id integer, title text,
                created_from_update_id integer, description text, owner_user_id text,
                owner_name text, created_at text
            );
            create table work_updates (
                id integer primary key, project_id integer, source_type text,
                source_ref text, summary text, changes_json text, created_at text
            );
            create table work_summary_inputs (
                id integer primary key, source_type text, source_ref text,
                payload_json text
            );
            insert into work_projects values (1, '旧项目', '{}', '2026-01-01');
        """)
    manifest = build_task_semantic_import_manifest(db_path, limit=100)
    assert len(manifest.items) == 1
    assert manifest.items[0].disposition == "history_only"
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "select count(*) from sqlite_master where name='business_projects'"
        ).fetchone()[0] == 0
