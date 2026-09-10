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
    assert initial["macro_f1_min"] == 0.95
    assert initial["category_precision_min"] == 0.95
    assert initial["category_validation_samples_min"] == 20
    assert initial["p95_latency_max_ms"] == 500.0
    changed = store.create_model_promotion_config(
        expected_current_version=initial["config_version"],
        macro_f1_min=0.96,
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
            macro_f1_min=0.96,
            category_precision_min=0.97,
            category_validation_samples_min=25,
            p95_latency_max_ms=450.0,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("macro_f1_min", float("nan")),
        ("macro_f1_min", 1.1),
        ("category_precision_min", -1),
        ("category_validation_samples_min", 0),
        ("category_validation_samples_min", 1.5),
        ("category_validation_samples_min", True),
        ("p95_latency_max_ms", float("inf")),
        ("p95_latency_max_ms", 0),
        ("macro_f1_min", True),
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
            "macro_f1_min",
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


def _transition(store, **overrides):
    return (
        dict(
            request_id="request-1",
            actor="derek",
            from_mode="agent_primary",
            to_mode="model_primary",
            from_model_id=None,
            target_model_id="model-1",
            promotion_config_version=store.current_model_promotion_config()[
                "config_version"
            ],
            status="applied",
            reason="validation passed",
        )
        | overrides
    )


def test_transition_journal_idempotency_order_restart(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    payload = _transition(store)
    first = store.record_model_mode_transition(**payload)
    assert store.record_model_mode_transition(**payload) == first
    for field, value in dict(
        actor="other", reason="different", status="failed", target_model_id="model-2"
    ).items():
        with pytest.raises(ValueError, match="conflict"):
            store.record_model_mode_transition(**(payload | {field: value}))
    second = store.record_model_mode_transition(
        **_transition(
            store,
            request_id="request-2",
            from_mode="model_primary",
            from_model_id="model-1",
            to_mode="agent_primary",
            target_model_id=None,
        )
    )
    reopened = EmailStore(store.path)
    assert reopened.get_model_mode_transition("request-1") == first
    assert reopened.get_model_mode_transition("missing") is None
    assert reopened.list_model_mode_transitions() == [second, first]


@pytest.mark.parametrize(
    "changes",
    [
        {"request_id": " "},
        {"actor": ""},
        {"actor": 1},
        {"reason": None},
        {"from_mode": "unknown"},
        {"to_mode": "unknown"},
        {"status": "pending"},
        {"from_model_id": "unexpected"},
        {"from_mode": "model_primary"},
        {"target_model_id": None},
        {"to_mode": "agent_primary"},
        {"promotion_config_version": "missing"},
    ],
)
def test_transition_invalid_payload_is_not_written(tmp_path, changes):
    store = EmailStore(tmp_path / "email.sqlite3")
    with pytest.raises(ValueError):
        store.record_model_mode_transition(**_transition(store, **changes))
    assert store.list_model_mode_transitions() == []


@pytest.mark.parametrize("status", ["rejected", "failed"])
def test_unsuccessful_transition_can_record_missing_candidate(tmp_path, status):
    store = EmailStore(tmp_path / "email.sqlite3")
    result = store.record_model_mode_transition(
        **_transition(
            store,
            status=status,
            target_model_id=None,
            reason="no candidate",
        )
    )
    assert result["status"] == status


def test_config_cas_serializes_competing_writers(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    initial = store.current_model_promotion_config()
    barrier = Barrier(2)

    def create():
        barrier.wait()
        try:
            return store.create_model_promotion_config(
                expected_current_version=initial["config_version"],
                macro_f1_min=0.96,
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


def test_v33_migration_backup_and_restart(tmp_path):
    path = tmp_path / "email.sqlite3"
    initial_store = EmailStore(path)
    category = initial_store.update_category_descriptions(
        "work",
        core_description="Existing custom description",
        include=["customers"],
        exclude=["personal"],
        threshold=0.96,
        enabled=True,
        description_version="custom-desc",
        config_version="custom-config",
    )
    with sqlite3.connect(path) as db:
        db.execute("drop table email_model_mode_transitions")
        db.execute("drop table email_model_promotion_configs")
        db.execute("drop table email_category_description_revisions")
        db.execute("delete from email_schema_migrations")
        db.execute("insert into email_schema_migrations values (33, 'before')")
        with sqlite3.connect(tmp_path / "before.sqlite3") as backup:
            db.commit()
            db.backup(backup)
    store = EmailStore(path)
    initial = store.current_model_promotion_config()
    assert EmailStore(path).list_model_promotion_configs() == [initial]
    assert store.list_category_description_revisions("work")[0]["config"] == category
    with sqlite3.connect(path) as db:
        assert db.execute(
            "select version from email_schema_migrations order by version"
        ).fetchall() == [(33,), (34,), (35,), (36,)]
        assert db.execute("pragma foreign_key_check").fetchall() == []
    with sqlite3.connect(tmp_path / "before.sqlite3") as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert (
            db.execute("select max(version) from email_schema_migrations").fetchone()[0]
            == 33
        )


@pytest.mark.parametrize(
    "old,new",
    [
        ("macro_f1_min real not null", "macro_f1_min text not null"),
        ("check(macro_f1_min >= 0 and macro_f1_min <= 1)", ""),
        ("on delete restrict", "on delete cascade"),
    ],
)
def test_model_control_schema_contract_is_strict(tmp_path, old, new):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute("pragma writable_schema=on")
        rows = db.execute(
            "select name, sql from sqlite_master where name in ('email_model_promotion_configs', 'email_model_mode_transitions')"
        ).fetchall()
        assert any(old in sql for _, sql in rows)
        for name, sql in rows:
            db.execute(
                "update sqlite_master set sql=? where name=?",
                (sql.replace(old, new), name),
            )
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(store.path)


def test_model_control_primary_key_contract_is_strict(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        sql = db.execute(
            "select sql from sqlite_master where name='email_model_promotion_configs'"
        ).fetchone()[0]
        sql = sql.replace(
            "config_version text primary key not null", "config_version text not null"
        )
        sql = sql.replace(
            "created_at text not null", "created_at text primary key not null"
        )
        db.execute("drop table email_model_promotion_configs")
        db.execute(sql)
    with pytest.raises(EmailPersistenceCorruption, match="required primary key"):
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
        "email_model_mode_transitions",
        "email_category_description_revisions",
    ],
)
def test_immutable_history_rejects_replace_with_recursive_triggers_off(tmp_path, table):
    store = EmailStore(tmp_path / "email.sqlite3")
    store.record_model_mode_transition(**_transition(store))
    with sqlite3.connect(store.path) as db:
        db.execute("pragma recursive_triggers=off")
        before = db.execute(f"select * from {table}").fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(f"insert or replace into {table} select * from {table} limit 1")
        assert db.execute(f"select * from {table}").fetchall() == before


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_model_mode_transitions",
        "email_category_description_revisions",
    ],
)
def test_insert_immutability_trigger_is_required_at_startup(tmp_path, table):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        db.execute(f"drop trigger trg_{table}_immutable_insert")
    with pytest.raises(EmailPersistenceCorruption, match="required trigger"):
        EmailStore(store.path)


@pytest.mark.parametrize(
    "table,primary_key",
    [
        ("email_model_promotion_configs", "config_version"),
        ("email_model_mode_transitions", "request_id"),
        ("email_category_description_revisions", "revision_id"),
    ],
)
@pytest.mark.parametrize("alias", ["rowid", "_rowid_", "oid"])
def test_immutable_history_has_no_hidden_replacement_key(
    tmp_path, table, primary_key, alias
):
    store = EmailStore(tmp_path / "email.sqlite3")
    store.record_model_mode_transition(**_transition(store))
    with sqlite3.connect(store.path) as db:
        db.execute("pragma recursive_triggers=off")
        before = db.execute(f"select * from {table}").fetchall()
        columns = [row[1] for row in db.execute(f"pragma table_info({table})")]
        source = [
            "'fresh-id'" if column == primary_key else column for column in columns
        ]
        with pytest.raises(sqlite3.OperationalError, match="no column named"):
            db.execute(
                f"insert or replace into {table} ({alias}, {', '.join(columns)}) "
                f"select 1, {', '.join(source)} from {table} limit 1"
            )
        assert db.execute(f"select * from {table}").fetchall() == before
        assert (
            next(row for row in db.execute("pragma table_list") if row[1] == table)[4]
            == 1
        )


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_model_mode_transitions",
        "email_category_description_revisions",
    ],
)
def test_without_rowid_is_required_by_history_schema_contract(tmp_path, table):
    store = EmailStore(tmp_path / "email.sqlite3")
    with sqlite3.connect(store.path) as db:
        sql = db.execute(
            "select sql from sqlite_master where name=?", (table,)
        ).fetchone()[0]
        assert "without rowid" in sql.lower()
        db.execute(f"drop table {table}")
        db.execute(sql.replace("without rowid", ""))
    with pytest.raises(EmailPersistenceCorruption, match="WITHOUT ROWID"):
        EmailStore(store.path)


def test_history_order_survives_frozen_and_backwards_clock(tmp_path, monkeypatch):
    store = EmailStore(tmp_path / "email.sqlite3")
    monkeypatch.setattr(
        store, "_account_now", lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    initial = store.current_model_promotion_config()
    changed = store.create_model_promotion_config(
        expected_current_version=initial["config_version"],
        macro_f1_min=0.96,
        category_precision_min=0.95,
        category_validation_samples_min=20,
        p95_latency_max_ms=500,
    )
    first = store.record_model_mode_transition(**_transition(store))
    second = store.record_model_mode_transition(
        **_transition(store, request_id="request-2")
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
    assert reopened.list_model_mode_transitions() == [second, first]
    assert second["created_at"] > first["created_at"]
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
        db.execute(
            "insert into email_category_description_revisions values (?, ?, ?, ?, ?)",
            (
                "bad-revision",
                "work",
                config["config_version"],
                json.dumps(config),
                "now",
            ),
        )
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(store.path)


def test_restart_rejects_incomplete_category_revision_snapshot(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    config = {"category_key": "work", "config_version": "incomplete"}
    with sqlite3.connect(store.path) as db:
        db.execute(
            "insert into email_category_description_revisions values (?, ?, ?, ?, ?)",
            ("bad-revision", "work", "incomplete", json.dumps(config), "now"),
        )
    with pytest.raises(EmailPersistenceCorruption):
        EmailStore(store.path)


@pytest.mark.parametrize(
    "table",
    [
        "email_model_promotion_configs",
        "email_model_mode_transitions",
        "email_category_description_revisions",
    ],
)
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_model_control_history_is_append_only(tmp_path, table, operation):
    store = EmailStore(tmp_path / "email.sqlite3")
    store.record_model_mode_transition(**_transition(store))
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            if operation == "delete":
                db.execute(f"delete from {table}")
            else:
                db.execute(f"update {table} set created_at='changed'")


def test_restart_rejects_invalid_transition_shape(tmp_path):
    store = EmailStore(tmp_path / "email.sqlite3")
    payload = _transition(store, target_model_id=None)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "insert into email_model_mode_transitions values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*payload.values(), "now"),
        )
    with pytest.raises(EmailPersistenceCorruption, match="target_model_id"):
        EmailStore(store.path)


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
