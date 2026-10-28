from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
import json
from threading import Barrier
from uuid import UUID

import pytest


from app.email_store import (
    EmailFolderBindingConflict,
    EmailPersistenceCorruption,
    EmailStore,
)


def test_promotion_config_defaults_history_and_restart(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.current_model_promotion_config()
    assert initial["micro_f1_min"] == 0.95
    assert initial["category_precision_min"] == 0.95
    assert initial["category_validation_samples_min"] == 20
    assert initial["p95_latency_max_ms"] == 500.0
    changed = store.create_model_promotion_config(
        expected_current_version=initial["config_version"],
        micro_f1_min=0.96,
        category_precision_min=0.97,
        category_validation_samples_min=25,
        p95_latency_max_ms=450.0,
    )
    assert changed["config_version"] != initial["config_version"]
    assert UUID(changed["config_version"]).version == 4
    reopened = EmailStore(store.path)
    assert reopened.current_model_promotion_config() == changed
    assert reopened.list_model_promotion_configs() == [changed, initial]
    with pytest.raises(ValueError, match="changed"):
        store.create_model_promotion_config(
            expected_current_version=initial["config_version"],
            micro_f1_min=0.96,
            category_precision_min=0.97,
            category_validation_samples_min=25,
            p95_latency_max_ms=450.0,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("micro_f1_min", float("nan")),
        ("micro_f1_min", 1.1),
        ("category_precision_min", -1),
        ("category_validation_samples_min", 0),
        ("category_validation_samples_min", 1.5),
        ("category_validation_samples_min", True),
        ("p95_latency_max_ms", float("inf")),
        ("p95_latency_max_ms", 0),
        ("micro_f1_min", True),
        ("category_precision_min", "0.95"),
        ("p95_latency_max_ms", True),
        ("category_validation_samples_min", 2**63),
    ],
)
def test_promotion_config_rejects_invalid_values(tmp_path, field, value):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.current_model_promotion_config()
    values = {
        key: initial[key]
        for key in (
            "micro_f1_min",
            "category_precision_min",
            "category_validation_samples_min",
            "p95_latency_max_ms",
        )
    }
    values[field] = value
    with pytest.raises(ValueError):
        store.create_model_promotion_config(
            expected_current_version=initial["config_version"],
            **values,
        )
    assert len(store.list_model_promotion_configs()) == 1










def test_config_cas_serializes_competing_writers(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.current_model_promotion_config()
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return store.create_model_promotion_config(
                expected_current_version=initial["config_version"],
                micro_f1_min=0.96,
                category_precision_min=0.95,
                category_validation_samples_min=20,
                p95_latency_max_ms=500,
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(store.list_model_promotion_configs()) == 2


def _bump_schema_cookie(db):
    # The schema check is cached per file and SQLite schema cookie; a hand edit of
    # sqlite_master leaves the cookie alone, so move it as a real DDL change would.
    cookie = db.execute("pragma schema_version").fetchone()[0]
    db.execute(f"pragma schema_version={cookie + 1}")


@pytest.mark.parametrize(
    "old,new",
    [
        ("promotion_micro_f1_min real generated always", "promotion_micro_f1_min text generated always"),
        ("promotion_micro_f1_min>=0 and promotion_micro_f1_min<=1", "promotion_micro_f1_min>=0"),
    ],
)
def test_model_control_schema_contract_is_strict(tmp_path, old, new):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute("pragma writable_schema=on")
        sql = db.execute("select sql from sqlite_master where name='configs'").fetchone()[0]
        assert old in sql
        db.execute(
            "update sqlite_master set sql=? where name='configs'",
            (sql.replace(old, new),),
        )
        _bump_schema_cookie(db)
    with pytest.raises(sqlite3.IntegrityError):
        EmailStore(store.path)


def test_model_control_primary_key_contract_is_strict(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        sql = db.execute(
            "select sql from sqlite_master where name='configs'"
        ).fetchone()[0]
        assert "id integer primary key autoincrement" in sql
        db.execute("pragma writable_schema=on")
        db.execute("update sqlite_master set sql=? where name='configs'", (
            sql.replace("id integer primary key autoincrement", "id integer not null"),
        ))
        _bump_schema_cookie(db)
    with pytest.raises(sqlite3.IntegrityError):
        EmailStore(store.path)


@pytest.mark.parametrize("operation", ["update", "refresh"])
def test_category_revision_history_and_cas(tmp_path, operation):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.get_category_config("work")
    assert store.list_category_description_revisions("work")[0]["config"] == initial
    values = dict(
        core_description="Updated work description",
        include=["customers"],
        exclude=["personal"],
        threshold=0.96,
        enabled=True,
        description_version="work-desc-v2",
        config_version="work-v2",
        expected_current_version=initial["config_version"],
    )
    method = store.update_category_descriptions
    if operation == "refresh":
        method = store.refresh_category_with_bindings
        values["bindings"] = []
    changed = method("work", **values)
    with pytest.raises(EmailFolderBindingConflict, match="changed"):
        method("work", **(values | {"config_version": "work-v3"}))
    reopened = EmailStore(store.path)
    history = reopened.list_category_description_revisions("work")
    assert [row["config"] for row in history] == [changed, initial]
    assert history[0]["revision_id"] != history[1]["revision_id"]
    assert reopened.get_category_config("work") == changed


@pytest.mark.parametrize("operation", ["update", "refresh"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("core_description", "Different work content"),
        ("include", ["different inclusion"]),
        ("exclude", ["different exclusion"]),
    ],
)
def test_category_description_version_cannot_rewrite_historical_content(
    tmp_path,
    operation,
    field,
    value,
):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.get_category_config("work")
    values = {
        key: initial[key]
        for key in (
            "core_description",
            "include",
            "exclude",
            "threshold",
            "enabled",
            "description_version",
        )
    }
    method = store.update_category_descriptions
    if operation == "refresh":
        method = store.refresh_category_with_bindings
        values["bindings"] = []
    changed = method(
        "work",
        **(
            values
            | {
                "core_description": "New current description",
                "description_version": "new-desc",
                "config_version": "new-config",
                "expected_current_version": initial["config_version"],
            }
        ),
    )
    history = store.list_category_description_revisions("work")
    with pytest.raises(EmailFolderBindingConflict, match="description_version"):
        method(
            "work",
            **(
                values
                | {
                    field: value,
                    "config_version": "another-config",
                    "expected_current_version": changed["config_version"],
                }
            ),
        )
    reopened = EmailStore(store.path)
    assert reopened.get_category_config("work") == changed
    assert reopened.list_category_description_revisions("work") == history


@pytest.mark.parametrize("operation", ["update", "refresh"])
def test_category_description_version_allows_duplicate_content_snapshots(
    tmp_path, operation
):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.get_category_config("work")
    values = {
        key: initial[key]
        for key in (
            "core_description",
            "include",
            "exclude",
            "threshold",
            "enabled",
            "description_version",
            "config_version",
        )
    }
    method = store.update_category_descriptions
    if operation == "refresh":
        method = store.refresh_category_with_bindings
        values["bindings"] = []
    duplicate = method("work", **values)
    changed = method(
        "work",
        **(
            values
            | {
                "threshold": 0.99,
                "config_version": "new-threshold-config",
                "expected_current_version": duplicate["config_version"],
            }
        ),
    )
    reopened = EmailStore(store.path)
    assert [
        entry["config"]
        for entry in reopened.list_category_description_revisions("work")
    ] == [changed, initial]
    assert duplicate == initial


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_category_description_revisions",
    ],
)
def test_immutable_history_rejects_replace_with_recursive_triggers_off(tmp_path, table):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute("pragma recursive_triggers=off")
        before = db.execute(
            "select * from configs where record_type=?", (table,)
        ).fetchall()
        assert before
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                "insert or replace into configs(record_type,legacy_key,payload_json) "
                "select record_type,legacy_key,payload_json"
                " "
                "from configs where record_type=? limit 1", (table,)
            )
        assert db.execute(
            "select * from configs where record_type=?", (table,)
        ).fetchall() == before


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_category_description_revisions",
    ],
)
def test_duplicate_history_trigger_is_required_at_startup(tmp_path, table):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        short = {
            "email_model_promotion_configs": "promotion",
            "email_category_description_revisions": "revision",
        }[table]
        db.execute(f"drop trigger trg_configs_{short}_duplicate")
    with pytest.raises(sqlite3.IntegrityError):
        EmailStore(store.path)


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_category_description_revisions",
    ],
)
@pytest.mark.parametrize("alias", ["rowid", "_rowid_", "oid"])
def test_immutable_history_cannot_replace_through_physical_alias(
    tmp_path, table, alias
):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute("pragma recursive_triggers=off")
        before = db.execute("select * from configs where record_type=?", (table,)).fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                f"insert or replace into configs({alias},record_type,legacy_key,"
                "payload_json) "
                "select id+100000,record_type,legacy_key,payload_json"
                " "
                "from configs where record_type=? limit 1", (table,)
            )
        assert db.execute("select * from configs where record_type=?", (table,)).fetchall() == before


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_category_description_revisions",
    ],
)
def test_public_identity_index_is_required_by_history_schema_contract(tmp_path, table):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute("drop index idx_configs_public")
    with pytest.raises(sqlite3.IntegrityError):
        EmailStore(store.path)


def test_history_order_survives_frozen_and_backwards_clock(tmp_path, monkeypatch):
    store = EmailStore(tmp_path / "email.sqlite3")
    monkeypatch.setattr(
        store, "_account_now", lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    initial = store.current_model_promotion_config()
    changed = store.create_model_promotion_config(
        expected_current_version=initial["config_version"],
        micro_f1_min=0.96,
        category_precision_min=0.95,
        category_validation_samples_min=20,
        p95_latency_max_ms=500,
    )
    category = store.get_category_config("work")
    values = {
        key: category[key]
        for key in (
            "core_description",
            "include",
            "exclude",
            "threshold",
            "enabled",
            "description_version",
        )
    }
    store.update_category_descriptions("work", **values, config_version="cat-v2")
    store.update_category_descriptions("work", **values, config_version="cat-v3")
    reopened = EmailStore(store.path)
    assert reopened.list_model_promotion_configs() == [changed, initial]
    assert changed["created_at"] > initial["created_at"]
    revisions = reopened.list_category_description_revisions("work")
    assert [row["config_version"] for row in revisions] == [
        "cat-v3",
        "cat-v2",
        category["config_version"],
    ]
    assert (
        revisions[0]["created_at"]
        > revisions[1]["created_at"]
        > revisions[2]["created_at"]
    )


@pytest.mark.parametrize("operation", ["update", "refresh"])
@pytest.mark.parametrize("cas", [False, True])
def test_category_save_cannot_reuse_config_token_for_different_content(
    tmp_path, operation, cas
):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.get_category_config("work")
    values = {
        key: initial[key]
        for key in (
            "core_description",
            "include",
            "exclude",
            "threshold",
            "enabled",
            "description_version",
            "config_version",
        )
    }
    method = store.update_category_descriptions
    if operation == "refresh":
        method = store.refresh_category_with_bindings
        values["bindings"] = []
    if cas:
        values["expected_current_version"] = initial["config_version"]
    with pytest.raises(EmailFolderBindingConflict, match="config_version"):
        method("work", **(values | {"threshold": 0.99}))
    assert store.get_category_config("work") == initial
    assert len(store.list_category_description_revisions("work")) == 1


@pytest.mark.parametrize("operation", ["update", "refresh"])
@pytest.mark.parametrize("reuse_current", [False, True])
def test_category_cas_requires_never_used_config_version(
    tmp_path, operation, reuse_current
):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.get_category_config("work")
    values = {
        key: initial[key]
        for key in (
            "core_description",
            "include",
            "exclude",
            "threshold",
            "enabled",
            "description_version",
        )
    }
    method = store.update_category_descriptions
    if operation == "refresh":
        method = store.refresh_category_with_bindings
        values["bindings"] = []
    changed = method("work", **(values | {"config_version": "new-config"}))
    with pytest.raises(EmailFolderBindingConflict, match="config_version"):
        method(
            "work",
            **(
                values
                | {
                    "config_version": changed["config_version"]
                    if reuse_current
                    else initial["config_version"],
                    "expected_current_version": changed["config_version"],
                }
            ),
        )
    assert store.get_category_config("work") == changed
    assert len(store.list_category_description_revisions("work")) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"core_description": None},
        {"include": []},
        {"exclude": "invalid"},
        {"threshold": "0.95"},
        {"enabled": "false"},
        {"actions": ["unknown"]},
        {"action_parameters": []},
        {"description_version": ""},
        {"updated_at": None},
    ],
)
def test_restart_validates_full_category_revision_snapshot(tmp_path, changes):
    store = EmailStore(tmp_path / "email.sqlite3")
    config = store.get_category_config("work") | changes
    with sqlite3.connect(store.path) as db:
        payload = json.loads(db.execute(
            "select payload_json from configs where record_type='email_category_description_revisions' "
            "and revision_category_key='work' limit 1"
        ).fetchone()[0])
        payload["config_json"] = json.dumps(config)
        db.execute(
            "insert into configs(record_type,legacy_key,payload_json) values(?,?,?)",
            ("email_category_description_revisions", json.dumps(["bad-revision"]),
             json.dumps(payload)),
        )
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(store.path)


def test_restart_rejects_incomplete_category_revision_snapshot(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    config = {"category_key": "work", "config_version": "incomplete"}
    with sqlite3.connect(store.path) as db:
        payload = json.loads(db.execute(
            "select payload_json from configs where record_type='email_category_description_revisions' "
            "and revision_category_key='work' limit 1"
        ).fetchone()[0])
        payload["config_version"] = "incomplete"
        payload["config_json"] = json.dumps(config)
        db.execute(
            "insert into configs(record_type,legacy_key,payload_json) values(?,?,?)",
            ("email_category_description_revisions", json.dumps(["bad-revision"]),
             json.dumps(payload)),
        )
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(store.path)


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_category_description_revisions",
    ],
)
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_model_control_history_is_append_only(tmp_path, table, operation):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            if operation == "delete":
                db.execute("delete from configs where record_type=?", (table,))
            else:
                db.execute(
                    "update configs set payload_json=json_set(payload_json,"
                    "'$.created_at','changed') where record_type=?", (table,)
                )




def test_new_category_initial_revision_is_preserved(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    from app.email_classifier_contracts import EmailAction

    created = store.create_category_with_bindings(
        category_key="partners",
        display_name="Partners",
        core_description="Partner mail",
        include=["partners"],
        exclude=["personal"],
        threshold=0.95,
        actions=(EmailAction.MOVE,),
        action_parameters={EmailAction.MOVE: {"target_folder": "Partners"}},
        enabled=True,
        description_version="partners-desc",
        config_version="partners-v1",
        bindings=[],
    )
    assert store.list_category_description_revisions("partners")[0]["config"] == created

