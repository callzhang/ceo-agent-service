"""Atomic paired primitive with sentinel markers; real callback tests live in callers."""

from contextlib import closing
from pathlib import Path
import json
import sqlite3

import pytest

from app import action_email_schema_release as release_module
from app import action_email_storage as email_storage
from app import action_storage as storage
from app import business_schema_release as business_module
from app.business_schema_release import _recreate_source, capture_business_source
from app.business_storage import insert_business
from app.email_current_schema import (
    create_current_email_migration_ledger,
    create_current_email_non_action_schema,
)
from tests.test_email_action_storage import PARENTS, CHILDREN, _cases, _source_tables
from tests.test_business_core_source import CAPTURE as BUSINESS_CAPTURE
from tests.test_provider_state_source import declarations as provider_declarations
from app.provider_state_schema_release import capture_provider_state_source
from app.provider_state_storage import insert_observation, insert_folder_generation
from app.provider_state_schema_release import provider_state_schema_is_current
from app.state_schema_release import (SOURCE_TABLES as STATE_SOURCE_TABLES,
    capture_state_source, state_schema_is_current)
from app.state_definitions import state_projection
from app.state_storage import insert_state, update_state_rows
from app.run_storage import create_run_schema
from app.configs_schema_transfer import SOURCE_TABLES as CONFIG_SOURCE_TABLES
from tests.configs_source_fixtures import capture as config_capture
from tests.test_configs_schema_release import install_observations


# Deliberate test markers, not allocated deployment versions.
TARGET_STORE = "unselected-email13-protocol-test"
TARGET_EMAIL = 1000051
ORIGINAL_STORE = ("2026-10-13.5", " original\x00store timestamp ")
ORIGINAL_LEDGER = ((1, " first\x00migration "), (51, " final\x00migration "))
ROOT = Path(__file__).resolve().parents[1]
BUSINESS_FRESH_DECLARATIONS = (
    Path(__file__).parent / "fixtures/business_four_kind_fresh_declarations.json"
)


@pytest.fixture(params=("actual", "fresh_candidate"))
def source(tmp_path, request):
    path = tmp_path / "paired.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table tasks(id integer primary key,record_type text)")
        create_run_schema(db, include_workbench=True)
        create_current_email_non_action_schema(db)
        config_variant = "actual" if request.param == "actual" else "fresh"
        native = {name: definition for name, definition in
                  config_capture()[config_variant]["tables"].items()
                  if name in CONFIG_SOURCE_TABLES and name not in (
                      "managed_skill_export_receipts", "runtime_skill_load_receipts")}
        for name in (*native, "email_training_snapshot_observations"):
            db.execute(f'drop table if exists "{name}"')
        for definition in native.values():
            db.execute(definition["sql"])
        for definition in native.values():
            for index in reversed(definition["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in definition["triggers"]:
                db.execute(trigger["sql"])
        receipts = json.loads((ROOT / "docs/superpowers/specs/2026-10-15-configs-load-receipt-dedup-probe.json").read_text())
        for name in ("managed_skill_export_receipts", "runtime_skill_load_receipts"):
            for obj in receipts["actual_frozen_receipt_metadata"][name]["objects"]:
                if obj["type"] == "table":
                    db.execute(obj["sql"])
            for obj in receipts["actual_frozen_receipt_metadata"][name]["objects"]:
                if obj["type"] != "table" and obj["sql"]:
                    db.execute(obj["sql"])
        install_observations(db, config_variant)
        # This fixture models the published Email51 source. The fresh current
        # factory owns the target, so replace only its synthetic provider unit
        # with the frozen actual/fresh source declarations.
        db.execute("drop table provider_state")
        for definition in provider_declarations(
            "actual" if request.param == "actual" else "fresh"
        ).values():
            db.execute(definition["sql"])
            for index in definition["indexes"]:
                if index["sql"] is not None:
                    db.execute(index["sql"])
        create_current_email_migration_ledger(db)
        state_tables = config_capture()[config_variant]["tables"]
        for kind in STATE_SOURCE_TABLES:
            db.execute(f'drop table if exists "{kind}"')
        for kind in STATE_SOURCE_TABLES:
            db.execute(state_tables[kind]["sql"])
        for kind in STATE_SOURCE_TABLES:
            for index in reversed(state_tables[kind]["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in state_tables[kind]["triggers"]:
                db.execute(trigger["sql"])
        db.execute("insert into service_state values('store_schema_version',?,?)", ORIGINAL_STORE)
        db.executemany("insert into email_schema_migrations values(?,?)", ORIGINAL_LEDGER)
        db.execute("create table unrelated_parent(id integer primary key)")
        db.execute("create table unrelated_child(id integer primary key,parent_id integer references unrelated_parent(id))")
        db.execute("insert into unrelated_child values(1,404)")
        storage.create_action_schema(db, include_reply=True, reply_variant=(
            "actual_no_memory_fk" if request.param == "actual" else "fresh_memory_fk"))
        for table in _source_tables(request.param).values():
            db.execute(table["sql"])
            for index in reversed(table["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in table["triggers"]:
                db.execute(trigger["sql"])
        external = [item for item in PARENTS[request.param]["external_schema_references"]
                    if item["type"] == "trigger"]
        external += CHILDREN["actual"]["external_schema_references"]
        for trigger in external:
            if trigger["name"] not in {
                item["name"] for item in native["email_accounts"]["triggers"]
            }:
                db.execute(trigger["sql"])
        db.execute("""insert into email_classifications(
            id,account_id,folder,uidvalidity,uid,stable_message_identity,
            confidence,margin,probabilities_json,model_id,config_version,status,
            classification_source
        ) values(11,'account','INBOX',1,1,'message',0.5,0.1,'{}','model',
                 'config','pending_feedback','model')""")
        for kind, values in _cases().items():
            db.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                       tuple(values.values()))
        db.execute("update email_actions set attempt_count=1")
        # Ordinary nullable IDs retain their structural ROWID independently.
        db.execute("insert into email_unsubscribe_cancellations(rowid,decision_json) values(-7,'{}')")
        for name in ("business_source_documents", "business_task_signals"):
            definition = BUSINESS_CAPTURE["fresh"]["tables"][name]
            db.execute(definition["sql"])
            for index in reversed(definition["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
            for trigger in definition["triggers"]:
                db.execute(trigger["sql"])
        _recreate_source(db, json.loads(BUSINESS_FRESH_DECLARATIONS.read_text()))
        db.execute(
            "insert into business_tasks(id,title,stage) "
            "values(-11,'paired source task','candidate')"
        )
        db.execute(
            "insert into business_anchors(id,anchor_type,anchor_ref,title) "
            "values(-14,'project','paired','paired anchor')"
        )
        db.execute(
            "insert into business_projects(id,canonical_anchor_id,title,registry_source) "
            "values(-15,-14,'paired project','synthetic')"
        )
        db.execute(
            "insert into business_project_context_revisions("
            "id,project_id,context_json,evidence_json) values(-16,-15,'{}','[]')"
        )
        assert capture_business_source(db)["variant"] == "fresh"
        db.commit()
    return path


@pytest.fixture
def source_with_business(source):
    return source


def _prepare(path):
    return release_module.prepare_action_email_schema_release(
        ROOT, path, target_store_version=TARGET_STORE, target_email_version=TARGET_EMAIL)


def _markers(db):
    current = db.execute("select 1 from main.sqlite_schema where name='state' collate nocase").fetchone() is not None
    source = state_projection("service_state") if current else "select * from main.service_state"
    return (
        db.execute(f"select value,updated_at from ({source}) where key='store_schema_version'").fetchone(),
        tuple(db.execute("select version,applied_at from email_schema_migrations order by version")),
    )


def _fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        rowids = tuple((kind, tuple(db.execute(f"select rowid,* from {kind} order by rowid")))
                       for kind in email_storage.SOURCE_TABLES
                       if kind in storage.EMAIL_ROWID_TYPES
                       and db.execute("select 1 from sqlite_schema where name=?", (kind,)).fetchone())
        return tuple(sorted(db.iterdump())), rowids


def _database_image(path):
    with closing(sqlite3.connect(path)) as db:
        return tuple(db.iterdump())


def test_paired_publication_contains_email_and_business(source_with_business):
    release = _prepare(source_with_business)
    release.apply()
    with closing(sqlite3.connect(source_with_business)) as db:
        assert db.execute(
            f"select value from ({state_projection('service_state')}) where key='store_schema_version'"
        ).fetchone() == (TARGET_STORE,)
        assert state_schema_is_current(db)
        assert not any(db.execute("select 1 from main.sqlite_schema where name=?", (kind,)).fetchone()
                       for kind in STATE_SOURCE_TABLES)
        assert db.execute(
            "select max(version) from email_schema_migrations"
        ).fetchone() == (TARGET_EMAIL,)
        assert db.execute(
            "select 1 from sqlite_schema where type='table' and name='business'"
        ).fetchone()
        assert not db.execute(
            "select 1 from sqlite_schema where type='table' and name in "
            "('business_tasks','business_projects','business_anchors',"
            "'business_project_context_revisions')"
        ).fetchone()
        assert provider_state_schema_is_current(db)


def test_provider_current_facts_survive_inverse_old_writes_and_reforward(source):
    with closing(sqlite3.connect(source)) as db:
        db.execute("insert into email_provider_observations "
                   "(account_id,stable_message_identity,state,provider_folder_id,"
                   "provider_folder_name,category_key,important,important_signals_json,observed_at) "
                   "values('account-a','message-a','available','inbox','Inbox',null,0,'[]','source-time')")
        db.execute("insert into email_provider_folder_observation_generations "
                   "values('account-a','inbox','source-time')")
        db.commit()
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        db.execute("begin immediate")
        insert_observation(db, {
            "account_id": "account-b", "stable_message_identity": "message-b",
            "state": "available", "provider_folder_id": "archive",
            "provider_folder_name": "Archive", "category_key": "finance",
            "important": 1, "important_signals_json": '["\\\\Flagged"]',
            "observed_at": "target-time",
        })
        insert_folder_generation(db, {
            "account_id": "account-b", "provider_folder_id": "archive",
            "observed_at": "target-time",
        })
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert _markers(db) == (ORIGINAL_STORE, ORIGINAL_LEDGER)
        assert db.execute("select account_id,category_key,important,observed_at "
                          "from email_provider_observations order by account_id").fetchall() == [
            ("account-a", None, 0, "source-time"),
            ("account-b", "finance", 1, "target-time"),
        ]
        db.execute("update email_provider_observations set category_key='legal' "
                   "where account_id='account-b'")
        db.commit()
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select category_key,typeof(important) from provider_state "
                          "where record_type='observation' and account_id='account-b'").fetchone() == (
            "legal", "integer")
        assert db.execute("select observed_at from provider_state "
                          "where record_type='folder_generation' and account_id='account-b'").fetchone() == (
            "target-time",)


def test_paired_inverse_preserves_new_business_facts_and_original_markers(source_with_business):
    release = _prepare(source_with_business)
    release.apply()
    with closing(sqlite3.connect(source_with_business)) as db:
        db.execute("pragma foreign_keys=on")
        db.execute("begin immediate")
        insert_business(db, "anchor", {
            "id": 321, "anchor_type": "project", "anchor_ref": "new",
            "title": "new anchor",
        })
        insert_business(db, "project", {
            "id": 322, "canonical_anchor_id": 321,
            "title": "new project", "registry_source": "test",
        })
        insert_business(db, "project_context_revision", {
            "id": 323, "project_id": 322,
            "context_json": '{"current":true}', "evidence_json": "[]",
        })
        insert_business(db, "task", {
            "id": 324, "title": "new task", "stage": "candidate",
        })
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source_with_business)) as db:
        assert _markers(db) == (ORIGINAL_STORE, ORIGINAL_LEDGER)
        assert db.execute(
            "select title from business_tasks where id=324"
        ).fetchone() == ("new task",)
        assert db.execute(
            "select canonical_anchor_id from business_projects where id=322"
        ).fetchone() == (321,)
        assert db.execute(
            "select project_id,context_json from "
            "business_project_context_revisions where id=323"
        ).fetchone() == (322, '{"current":true}')
        assert db.execute(
            "select anchor_ref from business_anchors where id=321"
        ).fetchone() == ("new",)
        assert db.execute("pragma foreign_key_check").fetchall() == [
            ("unrelated_child", 1, "unrelated_parent", 0)
        ]
    release.apply()
    release.verify_loaded()
    with closing(sqlite3.connect(source_with_business)) as db:
        assert db.execute(
            "select record_type,public_id from business "
            "where public_id between 321 and 324 order by public_id"
        ).fetchall() == [
            ("anchor", 321), ("project", 322),
            ("project_context_revision", 323), ("task", 324),
        ]


def test_email_then_business_failure_rolls_back_entire_pair(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)

    def fail_after_email(_db):
        raise RuntimeError("business injection after Email conversion")

    monkeypatch.setattr(release_module, "migrate_business_schema", fail_after_email)
    with pytest.raises(RuntimeError, match="after Email conversion"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_provider_failure_after_conversion_rolls_back_all_three_components(monkeypatch, source):
    release = _prepare(source)
    before = _database_image(source)
    convert = release_module.apply_provider_state_migration

    def fail_after_provider(db, metadata):
        convert(db, metadata)
        raise RuntimeError("provider conversion injection")

    monkeypatch.setattr(release_module, "apply_provider_state_migration", fail_after_provider)
    with pytest.raises(RuntimeError, match="provider conversion injection"):
        release.apply()
    assert _database_image(source) == before


def test_provider_inverse_failure_restores_complete_target(monkeypatch, source):
    release = _prepare(source)
    release.apply()
    before = _database_image(source)
    inverse = release_module.rollback_provider_state_migration

    def fail_after_provider_inverse(db, metadata):
        inverse(db, metadata)
        raise RuntimeError("provider inverse injection")

    monkeypatch.setattr(release_module, "rollback_provider_state_migration", fail_after_provider_inverse)
    with pytest.raises(RuntimeError, match="provider inverse injection"):
        release.rollback()
    assert _database_image(source) == before


def test_inverse_email_failure_rolls_back_business_inverse(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    release.apply()
    target = _database_image(source_with_business)

    def fail_after_business(_db, _metadata):
        raise RuntimeError("Email inverse injection")

    monkeypatch.setattr(release_module, "restore_email_action_schema", fail_after_business)
    with pytest.raises(RuntimeError, match="Email inverse injection"):
        release.rollback()
    assert _database_image(source_with_business) == target


def test_marker_correct_business_index_damage_is_rejected(source_with_business):
    release = _prepare(source_with_business)
    release.apply()
    with closing(sqlite3.connect(source_with_business)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("drop index idx_business_task_public_id")
        db.commit()
    with pytest.raises(ValueError, match="Business target declaration"):
        release.verify_loaded()


def test_business_source_preflight_failure_is_readonly(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)

    def fail_preflight(_db):
        raise RuntimeError("Business pre-DDL injection")

    monkeypatch.setattr(release_module, "capture_business_source", fail_preflight)
    with pytest.raises(RuntimeError, match="pre-DDL injection"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_first_child_rebuild_failure_rolls_back_both_migrations(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)
    create = business_module._create_children

    def fail_after_one_child(db, declarations, *, target):
        if target:
            name = next(iter(declarations))
            create(db, {name: declarations[name]}, target=True)
            raise RuntimeError("Business first-child injection")
        return create(db, declarations, target=False)

    monkeypatch.setattr(business_module, "_create_children", fail_after_one_child)
    with pytest.raises(RuntimeError, match="first-child injection"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_first_marker_then_second_marker_failure_is_atomic(source_with_business):
    release = _prepare(source_with_business)
    with closing(sqlite3.connect(source_with_business)) as db:
        db.execute(
            "create trigger fail_email_target before insert on email_schema_migrations "
            f"when new.version={release.target_email_version} begin "
            "select raise(abort,'Email target marker injection'); end"
        )
        db.commit()
    original = _database_image(source_with_business)
    with pytest.raises(sqlite3.IntegrityError, match="marker injection"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_failure_after_genuine_target_verification_rolls_back(source_with_business):
    release = _prepare(source_with_business)
    original = _database_image(source_with_business)
    verify = release._verify_target

    def verified_then_fail(db):
        verify(db)
        raise RuntimeError("after target verification")

    release._verify_target = verified_then_fail
    with pytest.raises(RuntimeError, match="after target verification"):
        release.apply()
    assert _database_image(source_with_business) == original


def test_first_business_inverse_ddl_failure_keeps_target(
    monkeypatch, source_with_business,
):
    release = _prepare(source_with_business)
    release.apply()
    target = _database_image(source_with_business)

    def fail_source_rebuild(_db, _source):
        raise RuntimeError("Business inverse DDL injection")

    monkeypatch.setattr(business_module, "_recreate_source", fail_source_rebuild)
    with pytest.raises(RuntimeError, match="inverse DDL injection"):
        release.rollback()
    assert _database_image(source_with_business) == target


def test_prepare_is_readonly_and_captures_exact_pair_schema_aliases_and_ledger(source):
    before = _fingerprint(source)
    release = _prepare(source)
    assert release.original_version == ORIGINAL_STORE
    assert release.versions == ORIGINAL_LEDGER
    with closing(sqlite3.connect(source)) as db:
        db.execute("begin")
        expected = email_storage.capture_email_action_source(db)
        expected["business"] = capture_business_source(db)
        expected["provider"] = capture_provider_state_source(db)
        expected["configs"] = release_module._project_config_source(db, expected["action_variant"])
        expected["state"] = capture_state_source(db)
        assert release.source_schema == expected
    assert release.source_fk == (("unrelated_child", 1, "unrelated_parent", 0),)
    assert _fingerprint(source) == before


def test_readonly_config_projection_accepts_independent_source_variants(source):
    with closing(sqlite3.connect(source)) as db:
        db.execute("begin")
        config_variant = release_module.capture_config_declarations(db)['variant']
        action_variant = ("fresh_memory_fk" if config_variant == "actual"
                          else "actual_no_memory_fk")
        projected = release_module._project_config_source(db, action_variant)
        assert projected["configs"]["variant"] == config_variant + "_email52"
        assert projected["action_variant"] == action_variant


def test_prepare_refuses_external_native_config_view_without_mutation(source):
    with closing(sqlite3.connect(source)) as db:
        db.execute("create view unrelated_config_reader as select id from managed_skills")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError, match="dependency"):
        _prepare(source)
    assert _fingerprint(source) == before


def test_publish_commits_both_markers_with_complete_target_then_inverse_is_repeatable(source):
    release = _prepare(source)
    before = _fingerprint(source)
    release.rollback()  # Unpublished source is already a valid inverse result.
    assert _fingerprint(source) == before
    release.apply()
    release.verify_loaded()
    release.finalize()
    with closing(sqlite3.connect(source)) as db:
        store, ledger = _markers(db)
        assert store[0] == TARGET_STORE
        assert ledger[:-1] == ORIGINAL_LEDGER
        assert ledger[-1] == (TARGET_EMAIL, store[1])
        assert storage.action_schema_is_current(db, include_reply=True,
            reply_variant=release.source_schema["action_variant"], include_email=True,
            include_skill_config_receipts=True)
        assert not set(email_storage.SOURCE_TABLES).intersection(
            row[0] for row in db.execute("select name from sqlite_schema where type='table'"))
    release.rollback()
    assert _fingerprint(source) == before
    release.rollback()
    release.apply()
    release.finalize()


def test_inverse_uses_current_facts_receipt_backfill_native_ids_and_allocator_waters(source):
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        db.execute("pragma foreign_keys=on")
        storage.update_email_action(db, "email_reply_receipts", 1,
            {"provider_receipt_json": '{"smtp_acceptance_id":"current-receipt"}'})
        storage.insert_email_action(db, "email_unsubscribe_cancellations",
            {"action_identity": None, "decision_json": '{"current":true}'}, source_rowid=-19)
        storage.insert_action(db, "email_action_attempts", _cases()["email_action_attempts"] | {
            "id": 81, "attempt_number": 2, "provider_result_id": "new-attempt-receipt"})
        storage.delete_action(db, "email_action_attempts", 81)
        storage.insert_action(db, "email_unsubscribe_steps", _cases()["email_unsubscribe_steps"] | {
            "id": 93, "sequence": 2})
        storage.delete_action(db, "email_unsubscribe_steps", 93)
        storage.insert_action(db, "reply_attempts", {
            "id": 73, "conversation_id": "new", "conversation_title": "new",
            "trigger_message_id": "new", "trigger_sender": "s", "trigger_text": "x",
            "action": "reply", "sensitivity_kind": "ordinary", "send_status": "pending"})
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert _markers(db) == (ORIGINAL_STORE, ORIGINAL_LEDGER)
        assert db.execute("select provider_receipt_json from email_reply_receipts").fetchone() == (
            '{"smtp_acceptance_id":"current-receipt"}',)
        assert db.execute("select rowid,action_identity,decision_json from email_unsubscribe_cancellations where rowid=-19").fetchone() == (
            -19, None, '{"current":true}')
        assert db.execute("select seq from sqlite_sequence where name='email_action_attempts'").fetchone() == (81,)
        assert db.execute("select seq from sqlite_sequence where name='email_unsubscribe_steps'").fetchone() == (93,)
        assert db.execute("select attempt_count from email_actions").fetchone() == (1,)
        assert db.execute("select * from (" + storage.REPLY_ATTEMPT_COMMON_SQL + ") where id=73").fetchone() is not None
        assert db.execute("select seq from sqlite_sequence where name='actions:reply_attempts'").fetchone() == (73,)
    release.apply()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select seq from sqlite_sequence where name='email_action_attempts'").fetchone() == (81,)


def test_repaired_target_indexes_preserve_current_config_writes_and_inverse(source):
    from app.configs_storage import insert_config
    from app.schedules_storage import insert_schedule
    from app.configs_definitions import config_projection
    from app.schedules_definitions import schedule_projection
    from app.configs_schema_transfer import _target_declarations

    release = _prepare(source)
    release.apply()
    target = _target_declarations()
    with closing(sqlite3.connect(source)) as db:
        for owner, name in (
            ("configs", "idx_configs_revision_number"),
            ("schedules", "idx_schedules_enabled"),
        ):
            canonical = next(item["sql"] for item in target[owner]["indexes"]
                             if item["name"] == name)
            db.execute(f"drop index {name}")
            db.execute(canonical)
        db.execute("begin immediate")
        insert_config(db, "managed_skills", {
            "id": 43, "name": "current-skill", "display_name": "Current Skill",
            "created_at": "current-time",
        })
        insert_schedule(db, {
            "id": 42, "name": "Current schedule", "prompt": "current",
            "cron_expression": "0 * * * *", "timezone": "UTC",
            "runtime_id": "codex_oauth", "enabled": 1,
        })
        db.commit()
    release.verify_loaded()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select name from managed_skills where id=43").fetchone() == ("current-skill",)
        assert db.execute("select name from scheduled_tasks where id=42").fetchone() == ("Current schedule",)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute(
            f"select name from ({config_projection('managed_skills')}) where id=43"
        ).fetchone() == ("current-skill",)
        assert db.execute(
            f"select name from ({schedule_projection()}) where id=42"
        ).fetchone() == ("Current schedule",)


@pytest.mark.parametrize("damage", ("store", "email", "timestamp", "ledger", "declaration", "provider_declaration"))
def test_prepare_and_publish_refuse_changed_source_without_mutation(source, damage):
    release = _prepare(source)
    with closing(sqlite3.connect(source)) as db:
        if damage == "store":
            db.execute("update service_state set value='unverified' where key='store_schema_version'")
        elif damage == "email":
            db.execute("insert into email_schema_migrations values(50,'future')")
            db.execute("delete from email_schema_migrations where version=51")
        elif damage == "timestamp":
            db.execute("update service_state set updated_at='changed' where key='store_schema_version'")
        elif damage == "ledger":
            db.execute("update email_schema_migrations set applied_at='changed' where version=1")
        elif damage == "provider_declaration":
            db.execute("create index unknown_provider_source on email_provider_observations(observed_at)")
        else:
            db.execute("create index unknown_email_plan_index on email_action_plans(created_at)")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError):
        release.apply()
    assert _fingerprint(source) == before
    if damage in ("store", "email", "declaration", "provider_declaration"):
        with pytest.raises(ValueError):
            _prepare(source)
        assert _fingerprint(source) == before


@pytest.mark.parametrize("stage", ("before_ddl", "after_capture", "during_conversion", "after_first_marker", "before_commit"))
def test_publication_failure_rolls_back_ddl_markers_and_all_source_facts(source, monkeypatch, stage):
    release = _prepare(source)
    before = _fingerprint(source)

    def fail(*args, **kwargs):
        raise RuntimeError("injected paired publication failure")

    if stage == "before_ddl":
        monkeypatch.setattr(release_module, "capture_email_action_source", fail)
    elif stage == "after_capture":
        monkeypatch.setattr(release_module, "migrate_email_action_schema", fail)
    elif stage == "during_conversion":
        original = email_storage._insert_email_raw

        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(email_storage, "_insert_email_raw", interrupted)
    else:
        connect = sqlite3.connect

        class InterruptedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if stage == "after_first_marker" and sql.lower().startswith("insert into main.email_schema_migrations"):
                    assert self.execute(f"select value from ({state_projection('service_state')}) where key='store_schema_version'").fetchone() == (TARGET_STORE,)
                    fail()
                return super().execute(sql, parameters)

            def commit(self):
                if stage == "before_commit":
                    assert _markers(self)[0][0] == TARGET_STORE
                    assert _markers(self)[1][-1][0] == TARGET_EMAIL
                    fail()
                return super().commit()

        def interrupted_connect(*args, **kwargs):
            if args and args[0] == source:
                return connect(*args, **kwargs, factory=InterruptedConnection)
            return connect(*args, **kwargs)

        monkeypatch.setattr(release_module.sqlite3, "connect", interrupted_connect)
    with pytest.raises(RuntimeError, match="injected paired"):
        release.apply()
    assert _fingerprint(source) == before


@pytest.mark.parametrize("damage", ("store", "ledger", "action_declaration", "provider_index", "external_trigger", "account_guard", "alias"))
def test_unsupported_current_inverse_preserves_entire_database(source, damage):
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        if damage == "store":
            db.execute("begin")
            update_state_rows(db, "service_state", {"value": "unverified"},
                              where_sql="key='store_schema_version'")
        elif damage == "ledger":
            db.execute("update email_schema_migrations set applied_at='changed' where version=1")
        elif damage == "action_declaration":
            db.execute("create index unknown_email_action_index on actions(legacy_key)")
        elif damage == "provider_index":
            db.execute("drop index idx_provider_state_observation_folder")
        elif damage == "external_trigger":
            db.execute("drop trigger trg_email_reply_dispatch_blocks_plan_switch")
        elif damage == "account_guard":
            db.execute("drop trigger trg_configs_direct_account_delete")
        else:
            db.execute("insert into sqlite_sequence(name,seq) values('actions:unknown',100)")
        db.commit()
    before = _fingerprint(source)
    with pytest.raises(ValueError):
        release.rollback()
    assert _fingerprint(source) == before


def test_inverse_exception_after_ddl_restores_target_transaction(source, monkeypatch):
    release = _prepare(source)
    release.apply()
    before = _fingerprint(source)
    restore = release_module.restore_email_action_schema

    def interrupted(db, state):
        restore(db, state)
        raise RuntimeError("injected inverse failure after reconstruction")

    monkeypatch.setattr(release_module, "restore_email_action_schema", interrupted)
    with pytest.raises(RuntimeError, match="injected inverse"):
        release.rollback()
    assert _fingerprint(source) == before


def test_active_versions_constructors_select_coupled_target_and_noop_reprepare(tmp_path):
    from app.store import AutoReplyStore, STORE_SCHEMA_VERSION
    from app.email_store import EmailStore, EMAIL_SCHEMA_VERSION
    from app.store_schema_release import prepare_database_schema_release

    path = tmp_path / "active.sqlite3"
    AutoReplyStore(path)
    EmailStore(path)
    assert (STORE_SCHEMA_VERSION, EMAIL_SCHEMA_VERSION) == ("2026-10-14.1", 52)
    assert prepare_database_schema_release(ROOT, path) is None
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select 1 from sqlite_schema where name='email_action_plans'").fetchone() is None
        assert storage.action_schema_is_current(db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True, include_skill_config_receipts=True)


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


@pytest.mark.parametrize("object_type,name", [
    ("index", "idx_schedules_enabled"),
    ("index", "idx_configs_revision_number"),
    ("index", "idx_scheduled_task_runs_latest"),
    ("trigger", "trg_configs_runtime_config_update"),
    ("trigger", "trg_actions_runtime_skill_load_receipts_update"),
])
def test_current_constructor_repairs_only_known_missing_store_objects(
    tmp_path, object_type, name
):
    from app.store import AutoReplyStore
    import app.store as store_module
    from app.email_store import EmailStore

    path = tmp_path / "known-repair.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        expected = db.execute(
            "select sql from sqlite_schema where type=? and name=?", (object_type, name)
        ).fetchone()[0]
        db.execute(f"drop {object_type} {name}")
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "select sql from sqlite_schema where type=? and name=?", (object_type, name)
        ).fetchone() == (expected,)


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
    "run_index_wrong_owner", "run_trigger_wrong_owner",
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
                db.execute(f"create index {name} on conversations(conversation_id)")
        elif damage == "config_index_wrong_owner":
            db.execute("drop index idx_configs_revision_number")
            db.execute("create index idx_configs_revision_number on conversations(conversation_id)")
        elif damage == "config_trigger_wrong_owner":
            db.execute("drop trigger trg_configs_runtime_config_update")
            db.execute("create trigger trg_configs_runtime_config_update after insert on conversations begin select 1; end")
        elif damage == "run_index_wrong_owner":
            db.execute("drop index idx_scheduled_task_runs_latest")
            db.execute("create index idx_scheduled_task_runs_latest on conversations(conversation_id)")
        elif damage == "run_trigger_wrong_owner":
            db.execute("drop trigger trg_runs_workbench_turn_insert")
            db.execute("create trigger trg_runs_workbench_turn_insert after insert on conversations begin select 1; end")
        elif damage == "action_index_wrong_owner":
            db.execute("drop index idx_actions_skill_export_revision")
            db.execute("create index idx_actions_skill_export_revision on conversations(conversation_id)")
        else:
            db.execute("drop trigger trg_actions_runtime_skill_load_receipts_update")
            db.execute("create trigger trg_actions_runtime_skill_load_receipts_update after insert on conversations begin select 1; end")
    before = _database_image(path)
    with closing(sqlite3.connect(path)) as db:
        schema_version_before = db.execute("pragma schema_version").fetchone()[0]
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    with pytest.raises(sqlite3.IntegrityError):
        AutoReplyStore(path)
    assert _database_image(path) == before
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version_before


@pytest.mark.parametrize("kind,name,other_sql", (
    ("index", "idx_schedules_enabled",
     "create trigger idx_schedules_enabled after insert on conversations begin select 1; end"),
    ("trigger", "trg_configs_runtime_config_update",
     "create index trg_configs_runtime_config_update on conversations(conversation_id)"),
))
def test_current_repair_name_checks_preserve_independent_sqlite_namespaces(
    tmp_path, kind, name, other_sql
):
    from app.email_store import EmailStore
    from app.action_email_schema_release import configs_current_or_known_skill_guard_repair
    from app.configs_schema_transfer import _target_declarations

    path = tmp_path / "independent-namespaces.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"drop {kind} {name}")
        db.execute(other_sql)
        assert configs_current_or_known_skill_guard_repair(
            db, action_variant="fresh_memory_fk"
        )
        owner = "schedules" if kind == "index" else "configs"
        canonical = next(item["sql"] for item in _target_declarations()[owner]
                         ["indexes" if kind == "index" else "triggers"]
                         if item["name"] == name)
        db.execute(canonical)
        assert db.execute(
            "select count(*) from sqlite_schema where name=? collate nocase", (name,)
        ).fetchone()[0] == 2


def test_business_facts_after_prepare_are_converted_instead_of_restoring_capture(source):
    release = _prepare(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute("insert into email_unsubscribe_cancellations(rowid,action_identity,decision_json) values(23,'later','{}')")
        db.execute("update sqlite_sequence set seq=103 where name='email_action_attempts'")
        db.commit()
    before = _fingerprint(source)
    release.apply()
    release.rollback()
    assert _fingerprint(source) == before


@pytest.mark.parametrize("target_store,target_email", (
    ("2026-10-13.5", TARGET_EMAIL), ("", TARGET_EMAIL), (TARGET_STORE, 51),
    (TARGET_STORE, True), (TARGET_STORE, "unallocated"),
))
def test_protocol_requires_explicit_different_target_pair_without_mutation(source, target_store, target_email):
    before = _fingerprint(source)
    with pytest.raises(ValueError, match="explicit different target pair"):
        release_module.prepare_action_email_schema_release(
            ROOT, source, target_store_version=target_store, target_email_version=target_email)
    assert _fingerprint(source) == before


def test_inverse_failure_after_first_source_marker_preserves_target(source, monkeypatch):
    release = _prepare(source)
    release.apply()
    before = _fingerprint(source)
    connect = sqlite3.connect

    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.lower().startswith("delete from main.email_schema_migrations"):
                assert self.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == ORIGINAL_STORE
                raise RuntimeError("injected inverse marker failure")
            return super().execute(sql, parameters)

    def interrupted_connect(*args, **kwargs):
        if args and args[0] == source:
            return connect(*args, **kwargs, factory=InterruptedConnection)
        return connect(*args, **kwargs)

    monkeypatch.setattr(release_module.sqlite3, "connect", interrupted_connect)
    with pytest.raises(RuntimeError, match="injected inverse marker"):
        release.rollback()
    assert _fingerprint(source) == before


def test_state_forward_failure_restores_complete_source(monkeypatch, source):
    release = _prepare(source)
    before = _database_image(source)
    convert = release_module.migrate_state_schema

    def fail_after_state(db, metadata):
        convert(db, metadata)
        raise RuntimeError("State forward injection")

    monkeypatch.setattr(release_module, "migrate_state_schema", fail_after_state)
    with pytest.raises(RuntimeError, match="State forward injection"):
        release.apply()
    assert _database_image(source) == before


def test_state_inverse_failure_restores_complete_target(monkeypatch, source):
    release = _prepare(source)
    release.apply()
    before = _database_image(source)
    inverse = release_module.restore_state_schema

    def fail_after_state(db, metadata):
        inverse(db, metadata)
        raise RuntimeError("State inverse injection")

    monkeypatch.setattr(release_module, "restore_state_schema", fail_after_state)
    with pytest.raises(RuntimeError, match="State inverse injection"):
        release.rollback()
    assert _database_image(source) == before


def test_current_state_facts_survive_inverse_old_write_and_reforward(source):
    release = _prepare(source)
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        db.execute("begin immediate")
        insert_state(db, "seen_messages", {
            "message_id": "current", "conversation_id": "c", "seen_at": b"\x00\xffcurrent",
        }, source_rowid=0)
        insert_state(db, "service_state", {
            "key": "current-fact", "value": b"\x00\xffvalue", "updated_at": "current",
        }, source_rowid=-7)
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select rowid,message_id,seen_at,typeof(seen_at) "
                          "from seen_messages").fetchall() == [(0, "current", b"\x00\xffcurrent", "blob")]
        assert db.execute("select rowid,key,value,typeof(value) from service_state "
                          "where key='current-fact'").fetchall() == [(-7, "current-fact", b"\x00\xffvalue", "blob")]
        db.execute("insert into seen_messages(rowid,message_id,conversation_id,seen_at) "
                   "values(-8,'old-write','old','old-time')")
        db.execute("insert into service_state(rowid,key,value,updated_at) "
                   "values(0,'old-write','old-value','old-time')")
        db.commit()
    release.apply()
    with closing(sqlite3.connect(source)) as db:
        assert state_schema_is_current(db)
        assert db.execute(f"select rowid,message_id,seen_at,typeof(seen_at) from "
                          f"({state_projection('seen_messages', include_rowid=True)}) order by rowid").fetchall() == [
            (-8, "old-write", "old-time", "text"), (0, "current", b"\x00\xffcurrent", "blob")]
        assert db.execute(f"select rowid,key,value,typeof(value) from "
                          f"({state_projection('service_state', include_rowid=True)}) "
                          "where key in ('current-fact','old-write') order by rowid").fetchall() == [
            (-7, "current-fact", b"\x00\xffvalue", "blob"), (0, "old-write", "old-value", "text")]


def test_state_marker_blob_timestamp_and_pipe_payload_round_trip(monkeypatch, source):
    stamp = b"\x00\xfforiginal-timestamp"
    with closing(sqlite3.connect(source)) as db:
        db.execute("update service_state set updated_at=? where key='store_schema_version'", (stamp,))
        db.commit()
    release = _prepare(source)
    payloads = []

    def capture(arguments, **options):
        payloads.append(json.loads(options["input"]))

    monkeypatch.setattr(release_module.subprocess, "run", capture)
    release_module._migrate_pair(release)
    assert payloads[0]["original_version"][1] == {"t": "blob", "v": stamp.hex()}
    assert payloads[0]["source_schema"]["state"] == release.source_schema["state"]
    release.apply()
    release.rollback()
    with closing(sqlite3.connect(source)) as db:
        assert db.execute("select value,updated_at,typeof(updated_at) from service_state "
                          "where key='store_schema_version'").fetchone() == (
            ORIGINAL_STORE[0], stamp, "blob")
    release.apply()
    release.verify_loaded()


@pytest.mark.parametrize("current", (False, True))
def test_paired_state_declaration_drift_refuses_without_mutation(source, current):
    release = _prepare(source)
    if current:
        release.apply()
    with closing(sqlite3.connect(source)) as db:
        if current:
            db.execute("drop index idx_state_dispatcher_expiry")
        else:
            db.execute("create index unknown_seen_state_source on seen_messages(seen_at)")
        db.commit()
    before = _database_image(source)
    with pytest.raises(ValueError):
        if current:
            release.rollback()
        else:
            release.apply()
    assert _database_image(source) == before
