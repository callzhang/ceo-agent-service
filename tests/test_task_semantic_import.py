from __future__ import annotations
from tests.support.legacy_todo_facts import seed_legacy_work_project, seed_legacy_work_todo, revise_legacy_work_todo, seed_legacy_work_update

import json
import sqlite3
from dataclasses import replace

import pytest

import app.task_semantic_import as semantic_import
from app.provenance_storage import signal_projection
from app.action_business_definitions import business_action_projection
from app.business_legacy_fields import CURRENT_BUSINESS_PROJECTIONS as BUSINESS_PROJECTIONS
from app.business_storage import update_business
from app.action_storage import update_sync_action
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
        kind = {
            "business_tasks": "task", "business_projects": "project",
            "business_anchors": "anchor",
        }.get(table)
        relation = (f"({BUSINESS_PROJECTIONS[kind]})" if kind else
                    f"({signal_projection()})" if table == "business_task_signals" else table)
        return db.execute(f"select * from {relation} order by id").fetchall()


def _mapping_rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("select public_id,import_task_id from business "
                          "where record_type='work_todo' and import_task_id is not null "
                          "order by public_id").fetchall()


def _update_business(path, kind, public_id, values):
    with sqlite3.connect(path) as db:
        db.execute("begin immediate")
        update_business(db, kind, public_id, values)


def _legacy_store(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    project_id = seed_legacy_work_project(store, title="客户推进")
    todo_id = seed_legacy_work_todo(store, project_id=project_id, title="准备报价")
    update_id = seed_legacy_work_update(store,
        project_id=project_id, source_type="reply_attempt", source_ref="msg-1",
        summary="会议讨论了客户推进", changes_json="{}",
    )
    return store, project_id, todo_id, update_id


def test_current_import_owner_is_inline_and_legacy_container_is_absent(tmp_path):
    store = AutoReplyStore(tmp_path / "inline-owner.sqlite3")
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "select 1 from sqlite_schema where name='business_legacy_links'"
        ).fetchone() is None
        assert "import_task_id" in {
            row[1] for row in db.execute("pragma table_xinfo(business)")
        }
        assert "import_task_id" not in {
            column[0] for column in db.execute(
                f"select * from ({BUSINESS_PROJECTIONS['work_todo']}) limit 0"
            ).description
        }


def test_reader_rejects_old_manifest_version_before_item_decode(tmp_path):
    path = tmp_path / "old-manifest.json"
    path.write_text(json.dumps({
        "version": 2, "manifest_id": "old", "database_fingerprint": "old",
        "created_at": "old", "items": [{"old_shape": True}],
    }))
    with pytest.raises(ValueError, match="unsupported task semantic import manifest version"):
        read_task_semantic_import_manifest(path)


def _source_backed_todo(store, *, basis, source_type, author_user_id,
                        authorized=True, source_ref="source-1"):
    if basis == "meeting_action_item" and source_ref == "source-1":
        source_ref = "source-1#todos-sha256=abc"
    project_id = seed_legacy_work_project(store, title="报价相关工作")
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
    update_id = seed_legacy_work_update(store,
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
    todo_id = seed_legacy_work_todo(store,
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
    assert {item.legacy_kind for item in manifest.items} == {
        "work_projects", "work_todos", "work_updates"
    }
    assert len(_mapping_rows(store.path)) == 0

    result = apply_task_semantic_import_manifest(store.path, manifest)
    assert result.created == 0
    assert result.history_only == 3
    assert len(_mapping_rows(store.path)) == 0
    assert len(_rows(store.path, "business_task_signals")) == 0
    assert len(_rows(store.path, "business_tasks")) == 0
    assert store.get_work_project(project_id) is not None
    assert store.get_work_todo(todo_id) is not None
    assert store.list_work_updates(project_id)[0].id == update_id


def test_history_only_work_update_never_reads_import_owner(tmp_path, monkeypatch):
    store, _project_id, todo_id, update_id = _legacy_store(tmp_path)
    assert todo_id == update_id  # Distinct source kinds share this public ID.
    manifest = build_task_semantic_import_manifest(store.path)

    def unexpected_owner_lookup(*_args):
        raise AssertionError("history-only item queried the import owner")

    monkeypatch.setattr(semantic_import, "read_import_task", unexpected_owner_lookup)
    result = apply_task_semantic_import_manifest(store.path, manifest)
    assert result.history_only == len(manifest.items)


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
        task = db.execute(
            f"select * from ({BUSINESS_PROJECTIONS['task']})"
        ).fetchone()
        owner = db.execute(
            "select import_task_id from business where record_type='work_todo' and public_id=?",
            (todo_id,),
        ).fetchone()
        assert task["stage"] == "formal"
        assert task["formal_basis"] == basis
        assert owner["import_task_id"] == task["id"]
        assert db.execute(
            f"select count(*) from ({business_action_projection('business_task_evidence')}) "
            "where task_id=?", (task["id"],)
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


def test_compacted_source_is_explicitly_unavailable_for_semantic_import(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _, todo_id, _ = _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner",
    )
    before = build_task_semantic_import_manifest(store.path)
    assert next(item for item in before.items if item.legacy_row_id == todo_id
                and item.legacy_kind == "work_todos").disposition == "formal_task"
    with store._connect() as db:
        input_id = db.execute(
            f"select id from ({semantic_import.WORK_SUMMARY_INPUT_SQL})"
        ).fetchone()["id"]
    store.mark_work_summary_input_done(input_id)
    after = build_task_semantic_import_manifest(store.path)
    item = next(item for item in after.items if item.legacy_row_id == todo_id
                and item.legacy_kind == "work_todos")
    assert item.disposition == "history_only"
    assert item.reason == "source input unavailable after compaction"
    with pytest.raises(ValueError, match="fingerprint"):
        apply_task_semantic_import_manifest(store.path, before)


def test_candidate_evidence_is_retained_in_history_not_promoted(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    project_id = seed_legacy_work_project(store, title="客户推进")
    source_type, source_ref = "reply_attempt", "candidate-msg"
    summary = "可以研究美国报价方案"
    update_id = seed_legacy_work_update(store,
        project_id=project_id, source_type=source_type, source_ref=source_ref,
        summary=summary, changes_json=json.dumps({
            "candidate_task_evidence": {"source_excerpt": summary}
        }),
    )
    store.enqueue_work_summary_input(source_type, source_ref, json.dumps({
        "source": {"type": source_type, "ref": source_ref}, "summary": summary,
        "context": {"source_conversation_kind": "group"},
    }))
    seed_legacy_work_todo(store,
        project_id=project_id, title="研究美国报价方案", created_from_update_id=update_id,
    )
    manifest = build_task_semantic_import_manifest(store.path)
    item = next(item for item in manifest.items if item.legacy_kind == "work_todos")
    assert item.disposition == "history_only"
    assert item.semantic_task is None
    result = apply_task_semantic_import_manifest(store.path, manifest)
    assert result.history_only == len(manifest.items)
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            f"select count(*) from ({BUSINESS_PROJECTIONS['task']})"
        ).fetchone()[0] == 0
        assert db.execute(
            f"select count(*) from ({signal_projection()})"
        ).fetchone()[0] == 0


def test_cancelled_project_cache_does_not_create_official_registry_match(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    resolution = BusinessResolutionService(store)
    anchor_id = resolution.register_anchor(
        anchor_type="project", anchor_ref="registry:quote", title="正式报价项目"
    )
    resolution.register_official_project(
        anchor_id=anchor_id, registry_source="registry"
    )
    cached_only = seed_legacy_work_project(store, title="旧项目名称")
    same_title = seed_legacy_work_project(store, title="正式报价项目")
    manifest = build_task_semantic_import_manifest(store.path)
    by_id = {item.legacy_row_id: item for item in manifest.items}
    assert by_id[cached_only].disposition == "history_only"
    assert by_id[same_title].disposition == "history_only"

    apply_task_semantic_import_manifest(store.path, manifest)
    with sqlite3.connect(store.path) as db:
        assert _mapping_rows(store.path) == []
        assert db.execute(
            f"select count(*) from ({BUSINESS_PROJECTIONS['project']})"
        ).fetchone()[0] == 1


def test_changed_source_fingerprint_rejects_before_first_write(tmp_path):
    store, _, todo_id, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    revise_legacy_work_todo(store, todo_id, title="被更改的任务")
    with pytest.raises(ValueError, match="fingerprint"):
        apply_task_semantic_import_manifest(store.path, manifest)
    assert len(_mapping_rows(store.path)) == 0


def test_manifest_tampering_rejects_before_first_write(tmp_path):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    bad_item = replace(manifest.items[0], source_digest="0" * 64)
    bad_manifest = replace(manifest, items=(bad_item, *manifest.items[1:]))
    with pytest.raises(ValueError, match="manifest|digest"):
        apply_task_semantic_import_manifest(store.path, bad_manifest)
    assert len(_mapping_rows(store.path)) == 0


def test_limited_apply_reports_history_only_without_creating_links(tmp_path):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    first = apply_task_semantic_import_manifest(store.path, manifest, limit=1)
    assert first.created == 0
    assert first.history_only == 1
    rest = apply_task_semantic_import_manifest(store.path, manifest)
    assert rest.created == 0
    assert rest.history_only == len(manifest.items)
    assert len(_mapping_rows(store.path)) == 0


def test_plan_limit_selects_recent_legacy_rows_first(tmp_path):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    old_id = seed_legacy_work_project(store, title="旧工作")
    new_id = seed_legacy_work_project(store, title="近期工作")
    _update_business(store.path, "work_project", old_id,
                     {"last_activity_at": "2026-01-01 00:00:00"})
    _update_business(store.path, "work_project", new_id,
                     {"last_activity_at": "2026-09-24 00:00:00"})
    manifest = build_task_semantic_import_manifest(store.path, limit=1)
    assert manifest.items[0].legacy_row_id == new_id


def test_manifest_for_one_database_rejects_another_copy(tmp_path):
    source = AutoReplyStore(tmp_path / "source.sqlite3")
    seed_legacy_work_project(source, title="同一工作")
    target = AutoReplyStore(tmp_path / "target.sqlite3")
    seed_legacy_work_project(target, title="同一工作")
    manifest = build_task_semantic_import_manifest(source.path)
    with pytest.raises(ValueError, match="fingerprint"):
        apply_task_semantic_import_manifest(target.path, manifest)
    assert len(_mapping_rows(target.path)) == 0


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
            "select import_task_id from business where record_type='work_todo' and public_id=?",
            (second_todo_id,),
        ).fetchone()[0]
        db.execute(
            "update business set import_task_id=? where record_type='work_todo' and public_id=?",
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


def test_apply_rechecks_each_source_by_point_lookup_after_preflight(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner",
    )
    manifest = build_task_semantic_import_manifest(store.path)
    original = semantic_import._rows

    def refuse_whole_kind_read_in_writer(db, table):
        if db.execute("pragma query_only").fetchone()[0] == 0:
            raise AssertionError(f"whole-kind {table} materialized in writer")
        return original(db, table)

    monkeypatch.setattr(semantic_import, "_rows", refuse_whole_kind_read_in_writer)
    result = apply_task_semantic_import_manifest(store.path, manifest)
    assert result.created == 1
    assert result.history_only == 2
    with sqlite3.connect(store.path) as db:
        db.row_factory = sqlite3.Row
        statements = []
        points = []
        db.set_trace_callback(statements.append)
        for item in manifest.items:
            points.append(semantic_import._source_row(db, item.legacy_kind, item.legacy_row_id))
        db.set_trace_callback(None)
        for item, point in zip(manifest.items, points, strict=True):
            corpus = next(row for row in original(db, item.legacy_kind)
                          if row["id"] == item.legacy_row_id)
            assert point == corpus
            assert semantic_import._digest(point) == semantic_import._digest(corpus)
            if item.legacy_kind == "work_updates":
                # Retained native SQLite values have the same digest shape.
                assert type(point["project_id"]) is int
        assert len(statements) == len(manifest.items)
        for item, statement in zip(manifest.items, statements, strict=True):
            steps = [row[3] for row in db.execute("explain query plan " + statement)]
            index = ("idx_actions_public_id" if item.legacy_kind == "work_updates" else
                     "idx_business_public_id")
            assert any("SEARCH" in step and index in step for step in steps), (item.legacy_kind, steps)
            if item.legacy_kind == "work_updates":
                assert any("<expr>=?" in step for step in steps), steps
                assert not any("USE TEMP B-TREE FOR ORDER BY" in step for step in steps), steps
        db.execute("begin immediate")
        update_sync_action(db, "work_updates", 1, {"project_id": 0.375})
        db.commit()
        point = semantic_import._source_row(db, "work_updates", 1)
        corpus = original(db, "work_updates")[0]
        assert type(point["project_id"]) is float
        assert point == corpus
        assert semantic_import._digest(point) == semantic_import._digest(corpus)


@pytest.mark.parametrize("changed_source", ["work_updates", "work_summary_inputs"])
def test_per_item_transaction_rechecks_supporting_source_before_write(
    tmp_path, monkeypatch, changed_source
):
    store = AutoReplyStore(tmp_path / "legacy.sqlite3")
    _source_backed_todo(
        store, basis="explicit_assignment", source_type="reply_attempt",
        author_user_id="assigner",
    )
    for project in store.list_work_projects():
        _update_business(store.path, "work_project", project.id,
                         {"last_activity_at": "2026-01-01 00:00:00"})
    with sqlite3.connect(store.path) as db:
        db.execute("begin immediate")
        update_sync_action(db, "work_updates", 1, {"created_at": "2026-01-01 00:00:00"})
    for todo in store.list_work_todos():
        _update_business(store.path, "work_todo", todo.id,
                         {"updated_at": "2026-12-01 00:00:00"})
    manifest = build_task_semantic_import_manifest(store.path)
    assert manifest.items[0].legacy_kind == "work_todos"
    original_check = semantic_import._check_manifest

    def change_source_after_preflight(path, checked_manifest):
        original_check(path, checked_manifest)
        with store._connect() as db:
            db.execute("begin immediate")
            if changed_source == "work_updates":
                update_sync_action(db, "work_updates", 1, {"summary": "changed"})
            else:
                db.execute(
                    "update work_summary_inputs set source_ref='changed' "
                    "where task_id=(select task_id from work_summary_inputs where source_ref='source-1')"
                )

    monkeypatch.setattr(semantic_import, "_check_manifest", change_source_after_preflight)
    with pytest.raises(ValueError, match="supporting source digest"):
        apply_task_semantic_import_manifest(store.path, manifest, limit=1)
    assert len(_mapping_rows(store.path)) == 0


def test_manifest_roundtrip_and_version_validation(tmp_path):
    store, _, _, _ = _legacy_store(tmp_path)
    manifest = build_task_semantic_import_manifest(store.path)
    path = tmp_path / "manifest.json"
    write_task_semantic_import_manifest(manifest, path)
    assert read_task_semantic_import_manifest(path) == manifest
    old_payload = json.loads(path.read_text())
    old_payload["version"] = 2
    old_payload["items"][0]["official_project_registry_key"] = ""
    path.write_text(json.dumps(old_payload))
    with pytest.raises(ValueError, match="unsupported task semantic import manifest version"):
        read_task_semantic_import_manifest(path)
    with pytest.raises(ValueError, match="version"):
        apply_task_semantic_import_manifest(store.path, replace(manifest, version=999))
    assert len(_mapping_rows(store.path)) == 0


def test_plan_reads_current_business_source_without_creating_semantic_tasks(tmp_path):
    store = AutoReplyStore(tmp_path / "current.sqlite3")
    project_id = seed_legacy_work_project(store, title="旧项目")
    manifest = build_task_semantic_import_manifest(store.path, limit=100)
    assert len(manifest.items) == 1
    assert manifest.items[0].disposition == "history_only"
    assert manifest.items[0].legacy_row_id == project_id
    assert _rows(store.path, "business_tasks") == []
    assert _mapping_rows(store.path) == []
