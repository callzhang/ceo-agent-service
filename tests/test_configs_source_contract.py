"""Unselected native source behavior for the 15 config families."""
import sqlite3
import pytest

from tests.configs_source_fixtures import CONFIGS, capture, insert, source_db

pytest_plugins = ("test_native_copy_lifecycle_harness",)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_fifteen_config_identities_and_118_fields(variant):
    source = capture()
    assert tuple(source["groups"]["configs"]) == CONFIGS
    definitions = source[variant]["tables"]
    assert sum(len(definitions[name]["columns"]) for name in CONFIGS) == 118
    with source_db(variant) as db:
        assert [name for name in CONFIGS if definitions[name]["identity"]["autoincrement"]] == [
            "managed_skill_revisions", "managed_skills", "runtime_skill_configs",
        ]
        assert [name for name in CONFIGS if definitions[name]["identity"]["without_rowid"]] == [
            "email_category_description_revisions", "email_model_mode_transitions",
            "email_model_promotion_configs",
        ]
        for name in CONFIGS:
            expected = [column["name"] for column in definitions[name]["columns"]]
            assert [column[1] for column in db.execute(f"pragma table_xinfo({name})")] == expected
        assert db.execute("pragma quick_check").fetchone() == ("ok",)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_text_keys_keep_source_null_and_binary_rules(variant):
    with source_db(variant) as db:
        account = {
            "account_id": None, "display_name": "A", "email_address": "a@example.invalid",
            "imap_host": "imap", "imap_port": "993", "imap_tls": "1",
            "imap_username": "a", "imap_secret_reference": "secret-ref",
            "smtp_host": "smtp", "smtp_port": 465, "smtp_tls": 1,
            "smtp_username": "a", "smtp_secret_reference": "secret-ref",
            "enabled": 1, "scan_folders_json": "[]", "scan_interval_seconds": 60,
            "created_at": "t0", "updated_at": "t0",
        }
        insert(db, "email_accounts", account)
        insert(db, "email_accounts", account | {"email_address": "b@example.invalid"})
        assert db.execute("select count(*) from email_accounts where account_id is null").fetchone() == (2,)
        assert db.execute("select typeof(imap_port),typeof(imap_tls) from email_accounts limit 1").fetchone() == ("integer", "integer")
        insert(db, "email_accounts", account | {"account_id": "Account"})
        insert(db, "email_accounts", account | {"account_id": "account"})
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_accounts", account | {"account_id": "Account"})
        insert(db, "email_category_configs", {
            "category_key": None, "display_name": "A", "core_description": "A",
            "include_json": '["a"]', "exclude_json": '["b"]', "threshold": 0.5,
            "actions_json": "[]", "action_parameters_json": "{}", "enabled": 1,
            "description_version": "d1", "config_version": "c1", "updated_at": "t0",
        })
        assert db.execute("select category_key from email_category_configs").fetchone() == (None,)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_configs", {
                "category_key": " ", "display_name": "A", "core_description": "A",
                "include_json": '["a"]', "exclude_json": '["b"]', "threshold": 0.5,
                "actions_json": "[]", "action_parameters_json": "{}", "enabled": 1,
                "description_version": "d1", "config_version": "c1", "updated_at": "t0",
            })


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_three_autoincrement_sequences_and_three_without_rowid_tables(variant):
    with source_db(variant) as db:
        for table, columns in (
            ("managed_skills", {"name": "s", "display_name": "S"}),
            ("managed_skill_revisions", {"skill_id": 700, "revision_number": 1,
                                         "content": "x", "sha256": "digest", "source": "test"}),
            ("runtime_skill_configs", {"status": "pending_restart"}),
        ):
            # The revision's FK parent is inserted first; explicit skill id 700 is valid.
            if table == "managed_skills":
                insert(db, table, {"id": 700, **columns})
                db.execute("delete from managed_skills where id=700")
                insert(db, table, {"id": 700, **columns})
            else:
                if table == "managed_skill_revisions":
                    columns = {**columns, "skill_id": 700}
                insert(db, table, {"id": 900, **columns})
                if table == "runtime_skill_configs":
                    # Its immutable delete guard makes the high-water test use
                    # INSERT OR IGNORE on a failed CHECK instead of deletion.
                    assert insert(db, table, {"id": 901, "status": "bad"}, ignore=True).rowcount == 0
                else:
                    assert insert(db, table, {"id": 901, **columns, "revision_number": -1}, ignore=True).rowcount == 0
            assert db.execute("select seq from sqlite_sequence where name=?", (table,)).fetchone()[0] >= (901 if table != "managed_skills" else 700)
        for table in ("email_category_description_revisions", "email_model_mode_transitions", "email_model_promotion_configs"):
            with pytest.raises(sqlite3.OperationalError, match="no such column: rowid"):
                db.execute(f"select rowid from {table}").fetchall()

@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_category_binding_foreign_keys_and_cascades(variant):
    with source_db(variant) as db:
        values = {"account_id": "acct", "display_name": "A", "email_address": "a@example.invalid",
                  "imap_host": "imap", "imap_port": 993, "imap_tls": 1,
                  "imap_username": "a", "imap_secret_reference": "ref", "smtp_host": "smtp",
                  "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
                  "smtp_secret_reference": "ref", "enabled": 1, "scan_folders_json": "[]",
                  "scan_interval_seconds": 60, "created_at": "t", "updated_at": "t"}
        category = {"category_key": "legal", "display_name": "Legal",
                    "core_description": "Legal", "include_json": '["legal"]',
                    "exclude_json": '["other"]', "threshold": 0.5,
                    "actions_json": "[]", "action_parameters_json": "{}",
                    "enabled": 1, "description_version": "d1", "config_version": "c1",
                    "updated_at": "t"}
        binding = {"account_id": "acct", "category_key": "legal",
                   "provider_folder_id": "folder", "provider_folder_name": "Folder",
                   "binding_status": "active", "last_verified_at": "t"}
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding)
        insert(db, "email_accounts", values)
        insert(db, "email_category_configs", category)
        insert(db, "email_category_folder_bindings", binding)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding | {"account_id": "missing"})
        db.execute("delete from email_category_configs where category_key='legal'")
        assert db.execute("select count(*) from email_category_folder_bindings").fetchone() == (0,)
        assert db.execute("pragma foreign_key_check").fetchall() == []

@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_internal_fk_and_unique_rejections(variant):
    with source_db(variant) as db:
        missing_parents = (
            ("managed_skill_revisions", {"skill_id": 404, "revision_number": 1,
                                         "content": "x", "sha256": "h", "source": "test"}),
            ("runtime_feedback_iteration_capabilities", {"config_id": 404, "enabled": 1}),
            ("runtime_skill_bindings", {"config_id": 404, "skill_id": 404,
                                        "revision_id": 404, "enabled": 1, "load_order": 0}),
            ("scheduled_task_skill_refs", {"scheduled_task_id": 404,
                                           "skill_source": "managed", "skill_name": "x",
                                           "managed_skill_id": 404,
                                           "managed_revision_id": 404, "position": 0}),
            ("email_model_mode_transitions", {"request_id": "r", "actor": "a",
                                              "from_mode": "agent_primary", "to_mode": "model_primary",
                                              "promotion_config_version": "missing",
                                              "status": "applied", "reason": "", "created_at": "t"}),
        )
        for table, values in missing_parents:
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
                insert(db, table, values)
        insert(db, "scheduled_task_config_versions", {"snapshot_json": '{"a":1}'})
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            insert(db, "scheduled_task_config_versions", {"snapshot_json": '{"a":1}'})
        # Native source accepts any TEXT body here; JSON validation is a caller contract.
        insert(db, "scheduled_task_config_versions", {"snapshot_json": "invalid-json"})
        scope = {"account_id": "a", "target_type": "direct", "target_id": "person",
                 "display_name": "Person", "trigger_mode": "every_inbound_text"}
        insert(db, "wechat_reply_scopes", scope)
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            insert(db, "wechat_reply_scopes", scope)
        insert(db, "wechat_reply_scopes", scope | {"target_type": "group"})
        assert db.execute("select count(*) from wechat_reply_scopes").fetchone() == (2,)
        assert db.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_category_and_binding_checks(variant):
    with source_db(variant) as db:
        base = {"category_key": "legal", "display_name": "Legal",
                "core_description": "Legal", "include_json": '["legal"]',
                "exclude_json": '["other"]', "threshold": 0.5,
                "actions_json": "[]", "action_parameters_json": "{}",
                "enabled": 1, "description_version": "d1", "config_version": "c1",
                "updated_at": "t"}
        for changed in ({"include_json": "[]"}, {"exclude_json": "{}"},
                        {"threshold": 1.1}, {"enabled": 2},
                        {"actions_json": "bad"}):
            with pytest.raises(sqlite3.IntegrityError):
                insert(db, "email_category_configs", base | changed)
        insert(db, "email_category_configs", base)
        account = {"account_id": "acct", "display_name": "A",
                   "email_address": "a@example.invalid", "imap_host": "imap",
                   "imap_port": 993, "imap_tls": 1, "imap_username": "a",
                   "imap_secret_reference": "ref", "smtp_host": "smtp",
                   "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
                   "smtp_secret_reference": "ref", "enabled": 1,
                   "scan_folders_json": "[]", "scan_interval_seconds": 60,
                   "created_at": "t", "updated_at": "t"}
        insert(db, "email_accounts", account)
        binding = {"account_id": "acct", "category_key": "legal",
                   "provider_folder_id": "", "provider_folder_name": "Folder",
                   "binding_status": "active", "last_verified_at": "t"}
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, "email_category_folder_bindings", binding)
        insert(db, "email_category_folder_bindings", binding | {"binding_status": "missing"})
