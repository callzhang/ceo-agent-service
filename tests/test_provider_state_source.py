"""Published provider-state source behavior, reconstructed in owned SQLite."""

import ast
from contextlib import contextmanager
import csv
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from app.email_classifier_contracts import EmailClassification, EmailClassificationStatus
from app.email_store import EmailPersistenceCorruption, EmailStore


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/superpowers/specs/2026-10-15-database-redesign-config-state-native-source.json"
FIELD_MAP = ROOT / "docs/superpowers/specs/2026-10-11-database-redesign-field-map.csv"
OBS = "email_provider_observations"
GEN = "email_provider_folder_observation_generations"
TABLES = (OBS, GEN)
T1 = "2026-10-15T10:00:00+00:00"
T2 = "2026-10-15T10:01:00+00:00"
APP_ID = "account-a:message-id:<synthetic@example.invalid>"


def declarations(variant):
    source = json.loads(SOURCE.read_text())
    assert set(source["groups"]["provider_state"]) == set(TABLES)
    return {table: source[variant]["tables"][table] for table in TABLES}


@contextmanager
def source_db(variant):
    """Execute literal frozen DDL and explicit indexes; never touch real data."""
    specs = declarations(variant)
    db = sqlite3.connect(":memory:")
    try:
        for spec in specs.values():
            db.execute(spec["sql"])
        for spec in specs.values():
            for index in spec["indexes"]:
                if index["sql"] is not None:
                    db.execute(index["sql"])
        yield db
    finally:
        db.close()


def insert_observation(db, account="account-a", identity="message-a", **changes):
    row = {
        "account_id": account,
        "stable_message_identity": identity,
        "state": "available",
        "provider_folder_id": "inbox",
        "provider_folder_name": "Inbox",
        "category_key": None,
        "important": 0,
        "important_signals_json": "[]",
        "observed_at": T1,
    }
    row.update(changes)
    columns = tuple(row)
    db.execute(
        f"insert into {OBS} ({', '.join(columns)}) "
        f"values ({', '.join('?' for _ in columns)})",
        tuple(row.values()),
    )


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_literal_declarations_and_twelve_field_map_entries(variant):
    specs = declarations(variant)
    with FIELD_MAP.open(newline="") as handle:
        mapped = [row for row in csv.DictReader(handle) if row["old_table"] in TABLES]
    assert len(mapped) == 12
    assert {(row["old_table"], row["old_column"]) for row in mapped} == {
        (table, column["name"])
        for table, spec in specs.items()
        for column in spec["columns"]
    }
    assert {
        (row["old_table"], row["old_column"]):
        (row["sql_type"], int(row["not_null"]), int(row["primary_key_position"]),
         row["target_table"], row["target_field_or_disposition"])
        for row in mapped
    } == {
        (table, col["name"]):
        (col["type"], col["notnull"], col["pk"],
         "provider_state", f"provider_state.payload.{col['name']}")
        for table, spec in specs.items()
        for col in spec["columns"]
    }
    with source_db(variant) as db:
        for table, spec in specs.items():
            assert db.execute(
                "select sql from sqlite_master where type='table' and name=?", (table,)
            ).fetchone()[0] == spec["sql"]
            assert list(db.execute(f"pragma table_xinfo({table})")) == [
                (column["cid"], column["name"], column["type"], column["notnull"],
                 column["dflt_value"], column["pk"], column["hidden"])
                for column in spec["columns"]
            ]
            assert list(db.execute(f"pragma foreign_key_list({table})")) == []
            assert spec["foreign_keys"] == spec["triggers"] == spec["sequences"] == []
            assert spec["identity"] == {
                "without_rowid": False, "autoincrement": False,
                "integer_primary_key_rowid_alias": False,
            }
            assert list(db.execute(f"pragma index_list({table})")) == [
                (idx["seq"], idx["name"], idx["unique"], idx["origin"], idx["partial"])
                for idx in spec["indexes"]
            ]
            for idx in spec["indexes"]:
                assert list(db.execute(f"pragma index_xinfo({idx['name']})")) == [
                    (col["seqno"], col["cid"], col["name"], col["desc"], col["coll"],
                     col["key"])
                    for col in idx["columns"]
                ]


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_affinities_checks_optional_values_and_binary_identity(variant):
    with source_db(variant) as db:
        insert_observation(db, important="1", provider_folder_id=None,
                           provider_folder_name=None, important_signals_json="42")
        db.execute(f"insert into {GEN} values (?,?,?)", ("account-a", "inbox", T1))
        assert db.execute(
            f"select typeof(account_id),typeof(stable_message_identity),typeof(state),"
            "typeof(provider_folder_id),typeof(provider_folder_name),typeof(category_key),"
            "typeof(important),typeof(important_signals_json),typeof(observed_at) "
            f"from {OBS}"
        ).fetchone() == (
            "text", "text", "text", "null", "null", "null", "integer", "text", "text"
        )
        assert db.execute(
            f"select typeof(account_id),typeof(provider_folder_id),typeof(observed_at) "
            f"from {GEN}"
        ).fetchone() == ("text", "text", "text")
        insert_observation(db, identity="MESSAGE-A")
        insert_observation(db, account="account-b")
        db.execute(f"insert into {GEN} values (?,?,?)", ("account-a", "INBOX", T1))
        db.execute(f"insert into {GEN} values (?,?,?)", ("account-b", "inbox", T1))
        assert db.execute(f"select count(*) from {OBS}").fetchone()[0] == 3
        assert db.execute(f"select count(*) from {GEN}").fetchone()[0] == 3
        with pytest.raises(sqlite3.IntegrityError):
            insert_observation(db)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"insert into {GEN} values (?,?,?)", ("account-a", "inbox", T2))
        for column, bad in (
            ("account_id", " "), ("stable_message_identity", ""),
            ("state", "pending"), ("observed_at", " "), ("important", 2),
            ("important_signals_json", "{bad"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                insert_observation(db, identity=f"invalid-{column}", **{column: bad})
        for column, bad in (
            ("account_id", " "), ("provider_folder_id", ""), ("observed_at", " ")
        ):
            values = {"account_id": "invalid", "provider_folder_id": "folder",
                      "observed_at": T1}
            values[column] = bad
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"insert into {GEN} values (?,?,?)", tuple(values.values()))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"insert into {OBS}(account_id,stable_message_identity) "
                       "values ('account-z','message-z')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"insert into {GEN}(account_id,provider_folder_id) "
                       "values ('account-z','folder-z')")
        assert db.execute(f"select important_signals_json from {OBS} "
                          "where stable_message_identity='message-a'").fetchone()[0] == "42"


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_native_upsert_update_delete_rowid_and_query_plans(variant):
    with source_db(variant) as db:
        insert_observation(db)
        obs_rowid = db.execute(f"select rowid from {OBS}").fetchone()[0]
        db.execute(f"update {OBS} set category_key='legal'")
        db.execute(
            f"insert into {OBS}(account_id,stable_message_identity,state,observed_at) "
            "values ('account-a','message-a','excluded',?) "
            "on conflict(account_id,stable_message_identity) do update set "
            "state=excluded.state", (T2,)
        )
        assert db.execute(f"select rowid,state,observed_at from {OBS}").fetchone() == (
            obs_rowid, "excluded", T1
        )
        db.execute(f"insert into {GEN} values (?,?,?)", ("account-a", "inbox", T1))
        gen_rowid = db.execute(f"select rowid from {GEN}").fetchone()[0]
        db.execute(
            f"insert into {GEN} values (?,?,?) on conflict(account_id,provider_folder_id) "
            "do update set observed_at=excluded.observed_at",
            ("account-a", "inbox", T2)
        )
        assert db.execute(f"select rowid,observed_at from {GEN}").fetchone() == (
            gen_rowid, T2
        )
        db.execute(f"delete from {OBS}")
        db.execute(f"delete from {GEN}")
        insert_observation(db)
        db.execute(f"insert into {GEN} values (?,?,?)", ("account-a", "inbox", T1))
        assert db.execute(f"select rowid from {OBS}").fetchone()[0] == obs_rowid
        assert db.execute(f"select rowid from {GEN}").fetchone()[0] == gen_rowid
        queries = (
            (f"select state from {OBS} where account_id=? and stable_message_identity=?",
             "sqlite_autoindex_email_provider_observations_1"),
            (f"select stable_message_identity from {OBS} where account_id=? "
             "and provider_folder_id=?", "idx_email_provider_observations_folder"),
            (f"select observed_at from {GEN} where account_id=? and provider_folder_id=?",
             "sqlite_autoindex_email_provider_folder_observation_generations_1"),
        )
        for sql, index in queries:
            plan = db.execute("explain query plan " + sql, ("account-a", "inbox")).fetchall()
            assert any(index in row[3] for row in plan), plan


def observation(identity=APP_ID, account="account-a", folder="inbox", **changes):
    row = {
        "account_id": account, "stable_message_identity": identity,
        "provider_folder_id": folder, "provider_folder_name": folder.title(),
        "folder_role": "category", "bound_category_key": "legal",
        "folder_binding_status": "active",
        "important_signals": {"provider_important": False, "raw_signal_names": []},
    }
    row.update(changes)
    return row


def publish(store, rows, time=T1, *, authoritative=(), unavailable=(), accounts=None):
    store.record_current_provider_observations(
        rows, unavailable_folders=unavailable, authoritative_folders=authoritative,
        active_account_ids=accounts, observed_at=time,
    )


def classification():
    return EmailClassification.model_validate({
        "classification_id": 101, "stable_message_identity": APP_ID,
        "provider_locator": {
            "account_id": "account-a", "folder": "Inbox", "uidvalidity": 1,
            "uid": 101, "rfc_message_id": "<synthetic@example.invalid>",
            "thread_id": None,
        },
        "category": "legal", "confidence": 1.0, "margin": 1.0,
        "probabilities": {"legal": 1.0}, "model_id": "synthetic:model",
        "config_version": "synthetic:v1",
        "status": EmailClassificationStatus.PENDING_FEEDBACK,
        "classification_source": "model", "action_plan": None,
    })


def test_provider_method_asts_equal_genuine_published_source():
    current = (ROOT / "app/email_store.py").read_text()
    published = subprocess.check_output(
        ["git", "show", "7dadc492:app/email_store.py"], cwd=ROOT, text=True
    )
    names = {
        "record_current_provider_observations", "record_provider_important_signals",
        "get_provider_classification_state", "get_provider_classification_states",
        "_provider_state_from_observation",
    }

    def methods(source):
        cls = next(node for node in ast.parse(source).body
                   if isinstance(node, ast.ClassDef) and node.name == "EmailStore")
        return {
            node.name: ast.dump(node, include_attributes=False)
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        }

    assert methods(current) == methods(published)
    assert set(methods(current)) == names


def test_application_identity_refresh_reconciliation_and_projection(tmp_path):
    store = EmailStore(tmp_path / "provider.sqlite3")
    assert store.get_provider_classification_state(101) == {
        "state": "unavailable", "reason": "classification_missing"
    }
    publish(store, [observation(), observation("message-b", "account-b")],
            accounts=("account-a", "account-b"))
    with store._connect() as db:
        first = db.execute(f"select rowid,observed_at from {OBS} "
                           "where account_id='account-a'").fetchone()
        assert db.execute("select count(*) from email_classifications").fetchone()[0] == 0
    publish(store, [observation()], T2, accounts=None)
    with store._connect() as db:
        same = db.execute(f"select rowid,observed_at from {OBS} "
                          "where account_id='account-a'").fetchone()
        generation = db.execute(f"select observed_at from {GEN} "
                                "where account_id='account-a' and provider_folder_id='inbox'"
                                ).fetchone()[0]
        other = db.execute(f"select state from {OBS} "
                           "where account_id='account-b'").fetchone()[0]
    assert tuple(first) == tuple(same)
    assert first[1] == T1 and generation == T2 and other == "available"
    store.upsert_classification(
        classification(), sender="synthetic@example.invalid",
        subject="Synthetic", preview="Synthetic", model_text="Synthetic"
    )
    state = store.get_provider_classification_state(101)
    assert state["state"] == "categorized" and state["observed_at"] == T2
    page = store.get_provider_classification_states((101, 999))
    assert page[101] == state
    assert page[999] == {"state": "unavailable", "reason": "classification_missing"}

    publish(store, [observation(folder="archive", bound_category_key="finance")],
            "2026-10-15T10:02:00+00:00", accounts=("account-a", "account-b"))
    with store._connect() as db:
        moved = db.execute(f"select rowid,provider_folder_id from {OBS} "
                           "where account_id='account-a'").fetchone()
        assert db.execute(f"select count(*) from {OBS} "
                          "where account_id='account-a'").fetchone()[0] == 1
    assert moved[0] == first[0] and moved[1] == "archive"
    assert store.get_provider_classification_state(101)["category_key"] == "finance"
    publish(store, [], "2026-10-15T10:03:00+00:00",
            accounts=("account-a", "account-b"))
    assert store.get_provider_classification_state(101)["state"] == "categorized"
    publish(store, [], "2026-10-15T10:04:00+00:00",
            unavailable=("account-a:archive",), accounts=("account-a", "account-b"))
    with store._connect() as db:
        row = db.execute(f"select state,provider_folder_id from {OBS} "
                         "where account_id='account-a'").fetchone()
    assert tuple(row) == ("unavailable", "archive")
    publish(store, [observation(folder="archive")], "2026-10-15T10:05:00+00:00",
            accounts=("account-a", "account-b"))
    publish(store, [], "2026-10-15T10:06:00+00:00",
            authoritative=("account-a:archive",), accounts=("account-a", "account-b"))
    with store._connect() as db:
        tombstone = db.execute(f"select state,provider_folder_id from {OBS} "
                               "where account_id='account-a'").fetchone()
    assert tuple(tombstone) == ("unavailable", None)
    assert store.get_provider_classification_state(101) == {
        "state": "unavailable", "reason": "provider_truth_unavailable",
        "observed_at": "2026-10-15T10:06:00+00:00"
    }
    publish(store, [observation()], "2026-10-15T10:07:00+00:00", accounts=None)
    publish(store, [], "2026-10-15T10:08:00+00:00", accounts=())
    with store._connect() as db:
        assert {row[0] for row in db.execute(f"select state from {OBS}")} == {
            "unavailable"
        }


def test_application_signals_shape_and_transaction_rollback(tmp_path):
    store = EmailStore(tmp_path / "signals.sqlite3")
    assert not store.record_provider_important_signals(
        "account-a", APP_ID, (r"\Flagged",)
    )
    publish(store, [observation()], accounts=("account-a",))
    assert store.record_provider_important_signals(
        "account-a", APP_ID, (r"\Flagged",)
    )
    with store._connect() as db:
        important, signals = db.execute(
            f"select important,important_signals_json from {OBS}"
        ).fetchone()
    assert important == 1 and json.loads(signals) == [r"\Flagged"]
    publish(store, [], T2, authoritative=("account-a:inbox",), accounts=("account-a",))
    assert not store.record_provider_important_signals(
        "account-a", APP_ID, ("$Important",)
    )
    publish(store, [observation()], "2026-10-15T10:02:00+00:00",
            accounts=("account-a",))
    store.upsert_classification(
        classification(), sender="synthetic@example.invalid",
        subject="Synthetic", preview="Synthetic", model_text="Synthetic"
    )
    with store._connect() as db:
        db.execute(f"update {OBS} set important_signals_json='42'")
    with pytest.raises(EmailPersistenceCorruption, match="invalid shape"):
        store.get_provider_classification_state(101)
    with store._connect() as db:
        db.execute(f"update {OBS} set important_signals_json='[]'")
        before = tuple(db.execute(f"select * from {OBS}").fetchone())
        generation_before = db.execute(f"select observed_at from {GEN} "
                                       "where provider_folder_id='inbox'").fetchone()[0]
        db.execute(
            f"create trigger reject_provider_generation before update on {GEN} "
            "begin select raise(abort, 'synthetic generation failure'); end"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic generation failure"):
        publish(store, [observation(folder="inbox", bound_category_key="finance")],
                "2026-10-15T10:03:00+00:00", accounts=("account-a",))
    with store._connect() as db:
        assert tuple(db.execute(f"select * from {OBS}").fetchone()) == before
        assert db.execute(f"select observed_at from {GEN} "
                          "where provider_folder_id='inbox'").fetchone()[0] == generation_before


@pytest.mark.parametrize("state", ("unavailable", "excluded"))
def test_nonavailable_projection_returns_before_signal_shape_validation(tmp_path, state):
    store = EmailStore(tmp_path / f"{state}.sqlite3")
    publish(store, [observation()], accounts=("account-a",))
    store.upsert_classification(
        classification(), sender="synthetic@example.invalid",
        subject="Synthetic", preview="Synthetic", model_text="Synthetic"
    )
    with store._connect() as db:
        db.execute(
            f"update {OBS} set state=?, important_signals_json='42'",
            (state,),
        )
    expected = {
        "state": state,
        "reason": "provider_truth_" + state,
        "observed_at": T1,
    }
    assert store.get_provider_classification_state(101) == expected
    assert store.get_provider_classification_states((101,))[101] == expected
    with store._connect() as db:
        db.execute(f"update {OBS} set state='available'")
    with pytest.raises(EmailPersistenceCorruption, match="invalid shape"):
        store.get_provider_classification_state(101)
