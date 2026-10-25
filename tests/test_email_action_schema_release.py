"""Atomic paired primitive with sentinel markers; real callback tests live in callers."""

from contextlib import closing
from pathlib import Path
import json
import sqlite3

import pytest

from app import action_email_storage as email_storage
from app import action_storage as storage
from app.action_error_storage import SOURCE_TABLE_SQL as ERROR_SOURCE_SQL, SOURCE_INDEX_SQL as ERROR_INDEX_SQL
from app.action_feedback_storage import SOURCE_SQL as FEEDBACK_SOURCE_SQL, SOURCE_INDEX_SQL as FEEDBACK_INDEX_SQL, SOURCE_TRIGGER_SQL as FEEDBACK_TRIGGER_SQL
from app.action_feedback_definitions import FEEDBACK_KINDS


# Deliberate test markers, not allocated deployment versions.
TARGET_STORE = "unselected-email13-protocol-test"
TARGET_EMAIL = 1000051
ORIGINAL_STORE = ("2026-10-13.5", " original\x00store timestamp ")
ORIGINAL_LEDGER = ((1, " first\x00migration "), (51, " final\x00migration "))
ROOT = Path(__file__).resolve().parents[1]
BUSINESS_FRESH_DECLARATIONS = (
    Path(__file__).parent / "fixtures/business_four_kind_fresh_declarations.json"
)
BUSINESS_WORK_PROJECTS_DECLARATIONS = (
    Path(__file__).parent / "fixtures/business_work_projects_source_declarations.json"
)
TASK20_PAIRED_SOURCE = Path(__file__).parent / "fixtures/task20_missing_paired_source_declarations.json"
FOLLOWUP_SYNC_SOURCE = Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json"
TASK_DELIVERY_SOURCE = ROOT / "docs/superpowers/specs/2026-10-17-task-delivery-source-metadata.json"


def _install_later_action_sources(db, variant):
    frozen = json.loads(TASK_DELIVERY_SOURCE.read_text())[variant]
    objects = [item for profile in frozen.values() for item in profile["objects"]]
    for kind in ("table", "index", "trigger"):
        for object_type, _name, _table, sql in objects:
            if object_type == kind and sql:
                db.execute(sql)
    db.execute(ERROR_SOURCE_SQL[0 if variant == "actual" else 1])
    for sql in ERROR_INDEX_SQL:
        db.execute(sql)
    for name in FEEDBACK_KINDS:
        if db.execute("select 1 from sqlite_schema where type='table' and name=?", (name,)).fetchone() is None:
            db.execute(FEEDBACK_SOURCE_SQL[variant][name])
        for sql in FEEDBACK_INDEX_SQL[name]:
            index_name = sql.split(" INDEX ", 1)[1].split(" ", 1)[0].split("\n", 1)[0]
            if db.execute("select 1 from sqlite_schema where type='index' and name=?", (index_name,)).fetchone() is None:
                db.execute(sql)
    for sql in FEEDBACK_TRIGGER_SQL:
        trigger_name = sql.split(" TRIGGER ", 1)[1].split("\n", 1)[0]
        if db.execute("select 1 from sqlite_schema where type='trigger' and name=?", (trigger_name,)).fetchone() is None:
            db.execute(sql)


def _install_task20_paired_source(db):
    objects = json.loads(TASK20_PAIRED_SOURCE.read_text())["objects"]
    for object_type, _name, sql in objects:
        if object_type == "table" and sql and "virtual table" not in sql.lower():
            db.execute(sql)
    for object_type, _name, sql in objects:
        if object_type == "table" and sql and "virtual table" in sql.lower():
            db.execute(sql)
    for kind in ("index", "trigger"):
        for object_type, _name, sql in objects:
            if object_type == kind and sql:
                db.execute(sql)


def _fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        rowids = tuple((kind, tuple(db.execute(f"select rowid,* from {kind} order by rowid")))
                       for kind in email_storage.SOURCE_TABLES
                       if kind in storage.EMAIL_ROWID_TYPES
                       and db.execute("select 1 from sqlite_schema where name=?", (kind,)).fetchone())
        dump = tuple(sorted(
            statement for statement in db.iterdump()
            if not statement.startswith('INSERT INTO "codex_session_search_fts_data"')
        ))
        fts_content = (
            tuple(db.execute(
                "select rowid,title from codex_session_search_fts order by rowid"
            ))
            if db.execute(
                "select 1 from sqlite_schema where name='codex_session_search_fts'"
            ).fetchone()
            else None
        )
        return dump, rowids, fts_content


def _business_work_project_facts(path):
    with closing(sqlite3.connect(path)) as db:
        columns = tuple(row[1] for row in db.execute("pragma table_info(work_projects)"))
        retained = tuple(column for column in columns if column != "memory_context_json")
        selected = ",".join(f'"{column}"' for column in retained)
        values = tuple(tuple(row) for row in db.execute(
            f"select {selected} from work_projects order by rowid"
        ))
        types = tuple(tuple(row) for row in db.execute(
            f"select {','.join(f'typeof(\"{column}\")' for column in retained)} "
            "from work_projects order by rowid"
        ))
        cache = tuple(row[0] for row in db.execute(
            "select memory_context_json from work_projects order by rowid"
        )) if "memory_context_json" in columns else ()
    return retained, values, types, cache


def _assert_inverse_except_retired_work_project_cache(path, before, work_projects_before):
    after = _fingerprint(path)
    before_dump = tuple(item for item in before[0]
                        if not item.startswith('INSERT INTO "work_projects"'))
    after_dump = tuple(item for item in after[0]
                       if not item.startswith('INSERT INTO "work_projects"'))
    assert (after_dump, after[1], after[2]) == (before_dump, before[1], before[2])
    retained, values, types, cache = _business_work_project_facts(path)
    assert (retained, values, types) == work_projects_before[:3]
    assert cache == tuple("{}" for _ in cache)


def _database_image(path):
    with closing(sqlite3.connect(path)) as db:
        return tuple(db.iterdump())


def test_fresh_email_seeds_disabled_categories_for_preexisting_enabled_typed_account(tmp_path):
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.configs_storage import insert_config
    from app.configs_definitions import config_projection

    path = tmp_path / "preseeded-account.sqlite3"
    store = AutoReplyStore(path)
    with store._immediate_write_transaction() as db:
        insert_config(db, "email_accounts", {
            "account_id": "preseeded", "display_name": "Preseeded",
            "email_address": "preseeded@example.test", "imap_host": "imap.example.test",
            "imap_port": 993, "imap_tls": 1, "imap_username": "preseeded",
            "imap_secret_reference": "env:TEST_IMAP", "imap_move_mode": "move",
            "smtp_host": "smtp.example.test", "smtp_port": 465, "smtp_tls": 1,
            "smtp_username": "preseeded", "smtp_secret_reference": "env:TEST_SMTP",
            "enabled": 1, "scan_folders_json": '["INBOX"]',
            "scan_interval_seconds": 60, "created_at": "2026-10-15 00:00:00",
            "updated_at": "2026-10-15 00:00:00",
        })
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute(
            f"select enabled from ({config_projection('email_category_configs')})"
        ).fetchall()
        assert rows and all(row[0] == 0 for row in rows)


@pytest.mark.parametrize("damage", [
    "altered_store_index", "unique_store_index", "partial_store_index",
    "missing_email_index",
])
def test_current_constructor_refuses_nonrepairable_metadata_without_mutation(tmp_path, damage):
    from app.store import AutoReplyStore
    import app.store as store_module
    from app.email_store import EmailStore

    path = tmp_path / "malformed-current.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        if damage == "altered_store_index":
            db.execute("drop index idx_schedules_enabled")
            db.execute("create index idx_schedules_enabled on schedules(name)")
        elif damage == "unique_store_index":
            db.execute("drop index idx_schedules_enabled")
            db.execute("create unique index idx_schedules_enabled on schedules(enabled,deleted_at,id)")
        elif damage == "partial_store_index":
            db.execute("drop index idx_schedules_enabled")
            db.execute("create index idx_schedules_enabled on schedules(enabled,deleted_at,id) where enabled=1")
        else:
            db.execute("drop index idx_actions_email_email_action_plans_0")
    before = _database_image(path)
    with closing(sqlite3.connect(path)) as db:
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError):
        AutoReplyStore(path)
    assert _database_image(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version_before


@pytest.mark.parametrize("damage", (
    "schedule_wrong_owner", "schedule_casefold_owner", "schedule_view", "schedule_table",
    "config_index_wrong_owner", "config_trigger_wrong_owner",
    "action_index_wrong_owner", "action_trigger_wrong_owner",
))
def test_current_constructor_refuses_occupied_repair_name_before_mutation(tmp_path, damage):
    from app.store import AutoReplyStore
    import app.store as store_module
    from app.email_store import EmailStore

    path = tmp_path / "occupied-repair-name.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        if damage in ("schedule_wrong_owner", "schedule_casefold_owner", "schedule_view", "schedule_table"):
            db.execute("drop index idx_schedules_enabled")
            if damage == "schedule_view":
                db.execute("create view idx_schedules_enabled as select 1 as value")
            elif damage == "schedule_table":
                db.execute("create table idx_schedules_enabled(value integer)")
            else:
                name = ("IDX_SCHEDULES_ENABLED" if damage == "schedule_casefold_owner"
                        else "idx_schedules_enabled")
                db.execute(f"create index {name} on tasks(id)")
        elif damage == "config_index_wrong_owner":
            db.execute("drop index idx_configs_revision_number")
            db.execute("create index idx_configs_revision_number on tasks(id)")
        elif damage == "config_trigger_wrong_owner":
            db.execute("drop trigger trg_configs_runtime_config_update")
            db.execute("create trigger trg_configs_runtime_config_update after insert on tasks begin select 1; end")
        elif damage == "run_index_wrong_owner":
            db.execute("drop index idx_scheduled_task_runs_latest")
            db.execute("create index idx_scheduled_task_runs_latest on tasks(id)")
        elif damage == "run_trigger_wrong_owner":
            db.execute("drop trigger trg_runs_workbench_turn_insert")
            db.execute("create trigger trg_runs_workbench_turn_insert after insert on tasks begin select 1; end")
        elif damage == "action_index_wrong_owner":
            db.execute("drop index idx_actions_skill_export_revision")
            db.execute("create index idx_actions_skill_export_revision on tasks(id)")
        else:
            db.execute("drop trigger trg_actions_runtime_skill_load_receipts_update")
            db.execute("create trigger trg_actions_runtime_skill_load_receipts_update after insert on tasks begin select 1; end")
    before = _database_image(path)
    with closing(sqlite3.connect(path)) as db:
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError):
        AutoReplyStore(path)
    assert _database_image(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version_before


