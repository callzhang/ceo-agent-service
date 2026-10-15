from contextlib import closing, contextmanager
import sqlite3

import pytest

from app.action_storage import create_action_schema, insert_email_action
from app.configs_definitions import (
    CONFIG_FIELDS, CONFIG_FRESH_PROJECTIONS, CONFIG_PROJECTIONS,
)
from app.configs_storage import (
    create_configs_schema, delete_config, insert_config, update_config,
)
from tests.configs_source_fixtures import (
    capture, insert as source_insert, source_db,
)
from tests.test_email_action_storage import _cases
from tests.test_email_action_writes import _databases

def test_seven_declared_fields_and_order():
    actual = capture()["actual"]["tables"]
    assert len(CONFIG_FIELDS) == 7
    assert sum(map(len, CONFIG_FIELDS.values())) == 73
    with closing(sqlite3.connect(":memory:")) as db:
        create_configs_schema(db)
        assert [row[1] for row in db.execute("pragma table_xinfo(configs)")
                if row[6] == 0][:4] == ["id", "record_type", "legacy_key", "payload_json"]
        for kind, fields in CONFIG_FIELDS.items():
            assert fields == tuple(c["name"] for c in actual[kind]["columns"])
            assert tuple(col[0] for col in db.execute(CONFIG_PROJECTIONS[kind]).description) == fields
        assert db.execute("pragma quick_check").fetchone() == ("ok",)

def test_signed_rowids_and_reuse():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        create_action_schema(
            db, include_reply=True, reply_variant="fresh_memory_fk",
            include_email=True,
        )
        create_configs_schema(db)
        db.execute("begin")
        account = {
            "account_id": "base", "display_name": "A",
            "email_address": "a@example.invalid", "imap_host": "imap",
            "imap_port": 993, "imap_tls": 1, "imap_username": "a",
            "imap_secret_reference": "ref", "smtp_host": "smtp",
            "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
            "smtp_secret_reference": "ref", "enabled": 1,
            "scan_folders_json": "[]", "scan_interval_seconds": 60,
            "created_at": "t", "updated_at": "t",
        }
        for source_rowid, key in ((-7, "minus"), (0, "zero")):
            result = insert_config(db, "email_accounts",
                                   account | {"account_id": key},
                                   source_rowid=source_rowid)
            assert (result.lastrowid, result.rowcount) == (source_rowid, 1)
        first = insert_config(db, "email_accounts",
                              account | {"account_id": "first"})
        second = insert_config(db, "email_accounts",
                               account | {"account_id": "second"})
        assert (first.lastrowid, second.lastrowid) == (1, 2)
        assert delete_config(db, "email_accounts", 2) == 1
        assert insert_config(db, "email_accounts",
                             account | {"account_id": "reused"}).lastrowid == 2
        assert [row[0] for row in db.execute(
            "select cast(legacy_key as integer) from configs "
            "where record_type='email_accounts' order by cast(legacy_key as integer)"
        )] == [-7, 0, 1, 2]

VALID = {
    "email_accounts": {
        "account_id": "acct", "display_name": "A",
        "email_address": "A@Example.invalid", "imap_host": "imap",
        "imap_port": 993, "imap_tls": 1, "imap_username": "a",
        "imap_secret_reference": "secret-ref", "smtp_host": "smtp",
        "smtp_port": 465, "smtp_tls": 1, "smtp_username": "a",
        "smtp_secret_reference": "secret-ref", "enabled": 1,
        "scan_folders_json": "[]", "scan_interval_seconds": 60,
        "created_at": "t", "updated_at": "t",
    },
    "email_category_configs": {
        "category_key": "legal", "display_name": "Legal",
        "core_description": "Legal", "include_json": '["legal"]',
        "exclude_json": '["other"]', "threshold": 0.5,
        "actions_json": "[]", "action_parameters_json": "{}",
        "enabled": 1, "description_version": "d1",
        "config_version": "c1", "updated_at": "t",
    },
    "email_category_description_revisions": {
        "revision_id": "r1", "category_key": "legal",
        "config_version": "c1", "config_json": '{"raw": 1}',
        "created_at": "t",
    },
    "email_category_folder_bindings": {
        "account_id": "acct", "category_key": "legal",
        "provider_folder_id": "f1", "provider_folder_name": "Folder",
        "binding_status": "active", "last_verified_at": "t",
    },
    "email_model_mode_transitions": {
        "request_id": "t1", "actor": "actor",
        "from_mode": "agent_primary", "to_mode": "model_primary",
        "promotion_config_version": "p1", "status": "applied",
        "reason": "", "created_at": "t",
    },
    "email_model_promotion_configs": {
        "config_version": "p1", "micro_f1_min": 0.8,
        "category_precision_min": 0.7,
        "category_validation_samples_min": 5,
        "p95_latency_max_ms": 200.0, "created_at": "t",
    },
    "email_training_snapshots": {
        "snapshot_id": "s1", "snapshot_version": "v1",
        "description_version": "d1", "input_schema_version": "i1",
        "seed": 0, "observed_at": "t", "snapshot_digest": "a" * 64,
        "manifest_json": '{"raw": 1}', "created_at": "t",
    },
}
INSERT_ORDER = (
    "email_accounts", "email_category_configs",
    "email_model_promotion_configs", "email_category_description_revisions",
    "email_category_folder_bindings", "email_model_mode_transitions",
    "email_training_snapshots",
)


@contextmanager
def pair(variant):
    with source_db(variant) as source, closing(sqlite3.connect(":memory:")) as target:
        target.execute("pragma foreign_keys=on")
        create_action_schema(
            target, include_reply=True, reply_variant="fresh_memory_fk",
            include_email=True,
        )
        create_configs_schema(target)
        target.execute("begin")
        yield source, target


def insert_both(source, target, kind, values, *, source_rowid=None):
    args = {"source_rowid": source_rowid} if source_rowid is not None else {}
    columns = dict(values)
    if source_rowid is not None:
        columns = {"rowid": source_rowid, **columns}
    native = source_insert(source, kind, columns)
    stored = insert_config(target, kind, values, **args)
    assert stored.rowcount == native.rowcount == 1
    if kind in ("email_accounts", "email_category_configs",
                "email_category_folder_bindings", "email_training_snapshots"):
        assert stored.lastrowid == native.lastrowid
    return stored

@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_all_seven_source_projections(variant):
    with pair(variant) as (source, target):
        for kind in INSERT_ORDER:
            insert_both(source, target, kind, VALID[kind])
        declarations = capture()[variant]["tables"]
        for kind in INSERT_ORDER:
            fields = tuple(c["name"] for c in declarations[kind]["columns"])
            source_sql = "select " + ",".join(fields) + " from " + kind
            projection = (CONFIG_FRESH_PROJECTIONS if variant == "fresh"
                          else CONFIG_PROJECTIONS)[kind]
            assert target.execute(projection).fetchall() == source.execute(
                source_sql).fetchall(), kind
            target_types = "select " + ",".join(
                f"typeof({field})" for field in fields) + " from (" + projection + ")"
            source_types = "select " + ",".join(
                f"typeof({field})" for field in fields) + " from " + kind
            assert target.execute(target_types).fetchall() == source.execute(
                source_types).fetchall(), kind
        assert target.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_source_affinity_nullable_keys_and_no_invented_rules(variant):
    with pair(variant) as (source, target):
        account = VALID["email_accounts"] | {
            "account_id": None, "imap_port": "993", "imap_tls": "1",
        }
        insert_both(source, target, "email_accounts", account)
        insert_both(source, target, "email_accounts",
                    account | {"email_address": "same@Example.invalid"})
        insert_both(source, target, "email_accounts",
                    account | {"account_id": "other",
                               "email_address": "same@Example.invalid"})
        category = VALID["email_category_configs"] | {"category_key": None}
        insert_both(source, target, "email_category_configs", category)
        insert_both(source, target, "email_category_configs",
                    category | {"config_version": "c2"})
        assert source.execute(
            "select count(*) from email_accounts where account_id is null"
        ).fetchone() == target.execute(
            "select count(*) from configs where record_type='email_accounts' "
            "and public_key is null"
        ).fetchone() == (2,)
        assert source.execute(
            "select typeof(imap_port),typeof(imap_tls) from email_accounts limit 1"
        ).fetchone() == target.execute(
            "select typeof(account_imap_port),typeof(account_imap_tls) "
            "from configs where record_type='email_accounts' limit 1"
        ).fetchone() == ("integer", "integer")

BAD = (
    ("email_accounts", {"imap_port": 0}),
    ("email_accounts", {"imap_tls": 2}),
    ("email_accounts", {"scan_folders_json": "invalid-json"}),
    ("email_accounts", {"scan_interval_seconds": 0}),
    ("email_accounts", {"imap_move_mode": "archive"}),
    ("email_accounts", {"scan_read_state": "flagged"}),
    ("email_category_configs", {"category_key": " "}),
    ("email_category_configs", {"include_json": "[]"}),
    ("email_category_configs", {"exclude_json": "{}"}),
    ("email_category_configs", {"threshold": 1.1}),
    ("email_category_configs", {"actions_json": "invalid-json"}),
    ("email_category_configs", {"config_version": " "}),
    ("email_category_description_revisions", {"revision_id": " "}),
    ("email_category_description_revisions", {"config_json": "invalid-json"}),
    ("email_category_folder_bindings", {"binding_status": "unknown"}),
    ("email_category_folder_bindings", {"provider_folder_id": ""}),
    ("email_model_mode_transitions", {"from_mode": "unknown"}),
    ("email_model_mode_transitions", {"status": "pending"}),
    ("email_model_mode_transitions", {"promotion_config_version": "missing"}),
    ("email_model_promotion_configs", {"micro_f1_min": 1.1}),
    ("email_model_promotion_configs", {"category_validation_samples_min": 0}),
    ("email_model_promotion_configs", {"p95_latency_max_ms": float("inf")}),
    ("email_training_snapshots", {"seed": -1}),
    ("email_training_snapshots", {"snapshot_digest": "short"}),
    ("email_training_snapshots", {"manifest_json": "invalid-json"}),
    ("email_training_snapshots", {"frozen": 2}),
)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
@pytest.mark.parametrize("kind,change", BAD)
def test_each_email_kind_native_rejection(variant, kind, change):
    with pair(variant) as (source, target):
        if kind == "email_category_folder_bindings":
            insert_both(source, target, "email_accounts", VALID["email_accounts"])
            insert_both(source, target, "email_category_configs",
                        VALID["email_category_configs"])
        if kind == "email_model_mode_transitions":
            insert_both(source, target, "email_model_promotion_configs",
                        VALID["email_model_promotion_configs"])
        candidate = VALID[kind] | change
        with pytest.raises(sqlite3.Error):
            source_insert(source, kind, candidate)
        with pytest.raises(sqlite3.Error):
            insert_config(target, kind, candidate)

@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_without_rowid_keys_distinguish_blob_and_text(variant):
    with pair(variant) as (source, target):
        kind = "email_model_promotion_configs"
        base = VALID[kind]
        insert_both(source, target, kind, base | {"config_version": 7})
        insert_both(source, target, kind, base | {"config_version": b"7"})
        assert target.execute(
            "select legacy_key,typeof(public_key),public_key from configs "
            "where record_type=? order by id", (kind,),
        ).fetchall() == [
            ('["7"]', "text", "7"),
            ('[{"t":"blob","v":"37"}]', "blob", b"7"),
        ]
        assert source.execute(
            "select typeof(config_version),config_version "
            "from email_model_promotion_configs order by typeof(config_version) desc"
        ).fetchall() == [("text", "7"), ("blob", b"7")]
        with pytest.raises(sqlite3.IntegrityError, match="immutable model control history"):
            source_insert(source, kind, base | {"config_version": "7"})
        with pytest.raises(sqlite3.IntegrityError, match="immutable model control history"):
            insert_config(target, kind, base | {"config_version": "7"})
        for connection, table in ((source, kind), (target, "configs")):
            with pytest.raises(sqlite3.OperationalError if table == kind
                               else sqlite3.IntegrityError):
                if table == kind:
                    connection.execute(f"select rowid from {table}").fetchall()
                else:
                    update_config(connection, kind, "7", {"created_at": "later"})


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_binding_typed_parent_update_and_cascade(variant):
    with pair(variant) as (source, target):
        for kind in ("email_accounts", "email_category_configs",
                     "email_category_folder_bindings"):
            insert_both(source, target, kind, VALID[kind])
        with pytest.raises(sqlite3.IntegrityError):
            source.execute(
                "update email_category_configs set category_key='changed' "
                "where category_key='legal'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            update_config(target, "email_category_configs", 1,
                          {"category_key": "changed"})
        source.execute("delete from email_category_configs where category_key='legal'")
        assert delete_config(target, "email_category_configs", 1) == 1
        assert source.execute(
            "select count(*) from email_category_folder_bindings"
        ).fetchone() == target.execute(
            "select count(*) from configs "
            "where record_type='email_category_folder_bindings'"
        ).fetchone() == (0,)
        assert target.execute("pragma foreign_key_check").fetchall() == []


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_snapshot_only_freezes_with_all_source_fields_unchanged(variant):
    with pair(variant) as (source, target):
        values = VALID["email_training_snapshots"] | {"frozen": 0}
        insert_both(source, target, "email_training_snapshots", values,
                    source_rowid=-5)
        source.execute(
            "update email_training_snapshots set frozen=1,rowid=9 where rowid=-5"
        )
        assert update_config(target, "email_training_snapshots", -5,
                             {"frozen": 1}, new_source_rowid=9) == 1
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            source.execute(
                "update email_training_snapshots set seed=1 where rowid=9")
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            update_config(target, "email_training_snapshots", 9, {"seed": 1})
        assert source.execute(
            "select rowid,frozen from email_training_snapshots"
        ).fetchone() == target.execute(
            "select cast(legacy_key as integer),snapshot_frozen from configs "
            "where record_type='email_training_snapshots'"
        ).fetchone() == (9, 1)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_active_binding_unique_and_parent_fk(variant):
    with pair(variant) as (source, target):
        insert_both(source, target, "email_accounts", VALID["email_accounts"])
        for category in ("legal", "finance"):
            insert_both(source, target, "email_category_configs",
                        VALID["email_category_configs"] | {"category_key": category})
        first = VALID["email_category_folder_bindings"]
        insert_both(source, target, "email_category_folder_bindings", first)
        second = first | {"category_key": "finance"}
        with pytest.raises(sqlite3.IntegrityError):
            source_insert(source, "email_category_folder_bindings", second)
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(target, "email_category_folder_bindings", second)
        insert_both(source, target, "email_category_folder_bindings",
                    second | {"binding_status": "missing"})
        missing = first | {"account_id": "missing", "category_key": "finance"}
        with pytest.raises(sqlite3.IntegrityError):
            source_insert(source, "email_category_folder_bindings", missing)
        with pytest.raises(sqlite3.IntegrityError):
            insert_config(target, "email_category_folder_bindings", missing)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_immutable_history_and_snapshot_delete(variant):
    with pair(variant) as (source, target):
        for kind in ("email_category_description_revisions",
                     "email_model_promotion_configs"):
            insert_both(source, target, kind, VALID[kind])
            key = VALID[kind][CONFIG_FIELDS[kind][0]]
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                source.execute(f"update {kind} set created_at='later'")
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                update_config(target, kind, key, {"created_at": "later"})
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                source.execute(f"delete from {kind}")
            with pytest.raises(sqlite3.IntegrityError,
                               match="immutable model control history"):
                delete_config(target, kind, key)
        insert_both(source, target, "email_training_snapshots",
                    VALID["email_training_snapshots"])
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            source.execute("delete from email_training_snapshots")
        with pytest.raises(sqlite3.IntegrityError,
                           match="email training snapshot is immutable"):
            delete_config(target, "email_training_snapshots", 1)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_negative_only_rowid_advances_reuses_and_empty_starts_at_one(variant):
    with pair(variant) as (source, target):
        kind = "email_category_configs"
        insert_both(source, target, kind, VALID[kind], source_rowid=-7)
        first = VALID[kind] | {"category_key": "first"}
        native = source_insert(source, kind, first).lastrowid
        stored = insert_config(target, kind, first).lastrowid
        assert (native, stored) == (-6, -6)
        assert source.execute(
            "delete from email_category_configs where rowid=-6"
        ).rowcount == 1
        assert delete_config(target, kind, -6) == 1
        reused = VALID[kind] | {"category_key": "reused"}
        native = source_insert(source, kind, reused).lastrowid
        stored = insert_config(target, kind, reused).lastrowid
        assert (native, stored) == (-6, -6)
    with pair(variant) as (source, target):
        kind = "email_category_configs"
        native = source_insert(source, kind, VALID[kind]).lastrowid
        stored = insert_config(target, kind, VALID[kind]).lastrowid
        assert (native, stored) == (1, 1)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_max_rowid_uses_native_overflow_allocator(variant):
    with pair(variant) as (source, target):
        kind = "email_accounts"
        maximum = 2**63 - 1
        insert_both(source, target, kind, VALID[kind], source_rowid=maximum)
        candidate = VALID[kind] | {"account_id": "overflow"}
        native = source_insert(source, kind, candidate).lastrowid
        stored = insert_config(target, kind, candidate).lastrowid
        assert 0 < native < maximum
        assert 0 < stored < maximum
        assert target.execute(
            "select count(*) from configs where record_type=?",
            (kind,),
        ).fetchone() == (2,)

@pytest.mark.parametrize("kind,status,error", (
    ("email_actions", "processing", "email_direct_action_in_flight"),
    ("email_reply_dispatch_claims", "dispatching",
     "email_reply_dispatch_in_flight"),
    ("email_unsubscribe_claims", "dispatching",
     "email_unsubscribe_write_in_flight"),
))
def test_account_blockers_use_current_typed_action_predicate_fixture(kind, status, error):
    old_email, current = _databases()
    with closing(old_email), closing(current):
        create_configs_schema(current)
        current.commit()
        current.execute("begin")
        insert_config(current, "email_accounts",
                      VALID["email_accounts"] | {"account_id": "account"})
        cases = _cases()
        insert_email_action(current, "email_action_plans",
                            cases["email_action_plans"])
        insert_email_action(current, kind, cases[kind] | {"status": status})
        with pytest.raises(sqlite3.IntegrityError, match=error):
            update_config(current, "email_accounts", 1, {"display_name": "B"})
        with pytest.raises(sqlite3.IntegrityError, match=error):
            delete_config(current, "email_accounts", 1)
