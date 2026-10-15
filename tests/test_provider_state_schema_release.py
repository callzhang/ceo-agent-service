"""Literal-source controls for the unselected provider-state pure migration."""

import copy
import sqlite3

import pytest

from app.provider_state_schema_release import (
    apply_provider_state_migration,
    capture_provider_state_source,
    rollback_provider_state_migration,
)
from app.provider_state_storage import insert_observation as target_insert_observation
from tests.test_provider_state_source import (
    GEN, OBS, T1, T2, declarations, insert_observation, source_db,
)


def _facts(db, table, fields):
    select = ",".join(fields) + "," + ",".join(f"typeof({field})" for field in fields)
    return sorted(db.execute(f"select {select} from main.{table}"), key=repr)


def _source_facts(db):
    return {table: _facts(db, table, [col["name"] for col in spec["columns"]])
            for table, spec in declarations("actual").items()}


def _schema(db):
    return list(db.execute("select type,name,tbl_name,sql from main.sqlite_schema order by type,name"))


def _seed(db):
    insert_observation(db, identity="message-a", important="1", important_signals_json="42")
    insert_observation(db, identity="MESSAGE-A", state="excluded", provider_folder_id=None,
                       provider_folder_name=sqlite3.Binary(b"\x00\xff"),
                       category_key="12", important=None, important_signals_json=None)
    insert_observation(db, account="account-b", identity="message-a", state="unavailable",
                       provider_folder_id="SPAM", provider_folder_name=float("inf"),
                       category_key=sqlite3.Binary(b"\x01"),
                       important_signals_json='{"reason":"stale"}')
    db.execute(f"insert into main.{GEN} values (?,?,?)", ("account-a", "inbox", T1))
    db.execute(f"insert into main.{GEN} values (?,?,?)", ("account-a", "INBOX", T2))
    db.execute(f"insert into main.{GEN} values (?,?,?)", ("account-b", "inbox", T1))


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_empty_forward_inverse(variant):
    with source_db(variant) as db:
        metadata = capture_provider_state_source(db)
        assert metadata["variant"] == variant
        db.execute("BEGIN")
        apply_provider_state_migration(db, metadata)
        assert db.in_transaction
        assert db.execute("select name from main.sqlite_schema where name=?", (OBS,)).fetchone() is None
        rollback_provider_state_migration(db, metadata)
        assert db.in_transaction
        assert db.execute("select name from main.sqlite_schema where name='provider_state'").fetchone() is None
        db.rollback()


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_exact_named_values_storage_classes_declarations_and_unrelated_state(variant):
    with source_db(variant) as db:
        db.execute("create table unrelated(id integer primary key autoincrement, value text)")
        db.execute("insert into unrelated(value) values ('keep')")
        db.execute("insert into unrelated(value) values ('water')")
        db.execute("delete from unrelated where value='water'")
        db.execute("create table unrelated_child(id integer primary key, foreign key(id) references missing_parent(id))")
        _seed(db)
        before_facts = _source_facts(db)
        before_schema = _schema(db)
        before_water = db.execute("select * from sqlite_sequence").fetchall()
        before_fks = db.execute("pragma foreign_key_check").fetchall()
        metadata = capture_provider_state_source(db)
        assert set(metadata) == {"variant", "tables"}
        assert set(metadata["tables"]) == {OBS, GEN}
        assert metadata["tables"][OBS]["sql"] == declarations(variant)[OBS]["sql"]
        assert "rows" not in repr(metadata) and "message-a" not in repr(metadata)
        apply_provider_state_migration(db, metadata)
        assert not any(name in (OBS, GEN) for _, name, _, _ in _schema(db))
        assert not db.execute("select 1 from main.sqlite_schema where type='view' and name in (?,?)", (OBS, GEN)).fetchone()
        assert db.execute("select count(*) from main.provider_state").fetchone()[0] == 6
        assert db.execute("select sql from main.sqlite_schema where name='provider_state'").fetchone()[0].lower().count("autoincrement") == 0
        rollback_provider_state_migration(db, metadata)
        assert _source_facts(db) == before_facts
        assert _schema(db) == before_schema
        assert db.execute("select * from sqlite_sequence").fetchall() == before_water
        assert db.execute("pragma foreign_key_check").fetchall() == before_fks


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_inverse_reads_current_target_and_reforward_reads_current_source(variant):
    with source_db(variant) as db:
        _seed(db)
        metadata = capture_provider_state_source(db)
        apply_provider_state_migration(db, metadata)
        db.execute("update main.provider_state set state='unavailable', observed_at=? "
                   "where record_type='observation' and stable_message_identity='message-a' "
                   "and account_id='account-a'", (T2,))
        db.execute("update main.provider_state set payload_json=json_set(payload_json,'$.category_key','new') "
                   "where record_type='observation' and stable_message_identity='MESSAGE-A'")
        db.execute("delete from main.provider_state where record_type='observation' and account_id='account-b'")
        db.execute("update main.provider_state set observed_at=? where record_type='folder_generation' "
                   "and account_id='account-a' and provider_folder_id='inbox'", (T2,))
        db.execute("delete from main.provider_state where record_type='folder_generation' "
                   "and account_id='account-b'")
        target_insert_observation(db, {
            "account_id": "account-c", "stable_message_identity": "new", "state": "available",
            "provider_folder_id": "inbox", "provider_folder_name": None, "category_key": "9",
            "important": 1, "important_signals_json": "42", "observed_at": T1,
        })
        db.execute("insert into main.provider_state(record_type,account_id,provider_folder_id,observed_at,payload_json) "
                   "values('folder_generation','account-c','inbox',?,'{}')", (T1,))
        rollback_provider_state_migration(db, metadata)
        assert db.execute(f"select state,observed_at from main.{OBS} where account_id='account-a' "
                          "and stable_message_identity='message-a'").fetchone() == ("unavailable", T2)
        assert db.execute(f"select category_key from main.{OBS} where stable_message_identity='MESSAGE-A'").fetchone() == ("new",)
        assert db.execute(f"select count(*) from main.{OBS} where account_id='account-b'").fetchone() == (0,)
        assert db.execute(f"select important_signals_json from main.{OBS} where account_id='account-c'").fetchone() == ("42",)
        assert db.execute(f"select observed_at from main.{GEN} where account_id='account-a' "
                          "and provider_folder_id='inbox'").fetchone() == (T2,)
        db.execute(f"update main.{OBS} set important_signals_json='[\"star\"]' "
                   "where account_id='account-c'")
        db.execute(f"insert into main.{GEN} values('account-d','other',?)", (T2,))
        current = _source_facts(db)
        apply_provider_state_migration(db, metadata)
        rollback_provider_state_migration(db, metadata)
        assert _source_facts(db) == current


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_wrong_source_or_metadata_refused_without_ddl(variant):
    with source_db(variant) as db:
        metadata = capture_provider_state_source(db)
        before = _schema(db)
        candidates = [None, {}, {**metadata, "extra": 1},
                      {**metadata, "variant": "unknown"},
                      {**metadata, "tables": {OBS: metadata["tables"][OBS]}}]
        tampered = copy.deepcopy(metadata)
        tampered["tables"][OBS]["sql"] += " "
        candidates.append(tampered)
        db.execute("BEGIN")
        for candidate in candidates:
            with pytest.raises(ValueError):
                apply_provider_state_migration(db, candidate)
            assert _schema(db) == before
        db.execute(f"create index main.unexpected_source_index on {OBS}(state)")
        before = _schema(db)
        with pytest.raises(ValueError):
            capture_provider_state_source(db)
        with pytest.raises(ValueError):
            apply_provider_state_migration(db, metadata)
        assert _schema(db) == before


def test_partial_already_target_and_temp_shadows_refused():
    with source_db("actual") as db:
        metadata = capture_provider_state_source(db)
        db.execute("create temp table EMAIL_PROVIDER_OBSERVATIONS(x text)")
        db.execute("BEGIN")
        with pytest.raises(ValueError, match="TEMP"):
            apply_provider_state_migration(db, metadata)
        assert db.execute("select count(*) from temp.EMAIL_PROVIDER_OBSERVATIONS").fetchone() == (0,)
        db.execute("drop table temp.EMAIL_PROVIDER_OBSERVATIONS")
        db.execute(f"drop table main.{GEN}")
        before = _schema(db)
        with pytest.raises(ValueError):
            apply_provider_state_migration(db, metadata)
        assert _schema(db) == before
    with source_db("actual") as db:
        metadata = capture_provider_state_source(db)
        db.execute("BEGIN")
        apply_provider_state_migration(db, metadata)
        with pytest.raises(ValueError):
            apply_provider_state_migration(db, metadata)
        db.execute("create temp view PROVIDER_STATE as select 1")
        before = _schema(db)
        with pytest.raises(ValueError, match="TEMP"):
            rollback_provider_state_migration(db, metadata)
        assert _schema(db) == before


def test_replacement_index_name_collisions_refused_before_ddl():
    with source_db("actual") as db:
        db.execute("create table spare(value text)")
        db.execute("create index idx_provider_state_observation_folder on spare(value)")
        before = _schema(db)
        with pytest.raises(ValueError, match="target name"):
            capture_provider_state_source(db)
        assert _schema(db) == before
    with source_db("actual") as db:
        metadata = capture_provider_state_source(db)
        db.execute("BEGIN")
        apply_provider_state_migration(db, metadata)
        db.execute("create table spare(value text)")
        db.execute("create index idx_email_provider_observations_folder on spare(value)")
        before = _schema(db)
        with pytest.raises(ValueError, match="source name"):
            rollback_provider_state_migration(db, metadata)
        assert _schema(db) == before


def test_malformed_target_and_inverse_metadata_refused_without_ddl():
    with source_db("fresh") as db:
        metadata = capture_provider_state_source(db)
        db.execute("BEGIN")
        apply_provider_state_migration(db, metadata)
        before = _schema(db)
        tampered = copy.deepcopy(metadata)
        tampered["tables"][GEN]["sql"] += " "
        with pytest.raises(ValueError):
            rollback_provider_state_migration(db, tampered)
        assert _schema(db) == before
        db.execute("create index main.unknown_target_index on provider_state(account_id)")
        before = _schema(db)
        with pytest.raises(ValueError):
            rollback_provider_state_migration(db, metadata)
        assert _schema(db) == before


def test_missing_caller_transaction_and_inverse_dependency_refused():
    with source_db("actual") as db:
        metadata = capture_provider_state_source(db)
        with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
            apply_provider_state_migration(db, metadata)
        db.execute("BEGIN")
        apply_provider_state_migration(db, metadata)
        db.commit()
        with pytest.raises(sqlite3.ProgrammingError, match="caller transaction"):
            rollback_provider_state_migration(db, metadata)
        db.execute("create view target_reader as select state from main.provider_state")
        db.execute("BEGIN")
        before = _schema(db)
        with pytest.raises(ValueError):
            rollback_provider_state_migration(db, metadata)
        assert _schema(db) == before


def test_inverse_failure_rolls_back_only_with_caller_rollback(monkeypatch):
    with source_db("fresh") as db:
        _seed(db)
        metadata = capture_provider_state_source(db)
        apply_provider_state_migration(db, metadata)
        db.commit()
        before = _schema(db)
        target_rows = list(db.execute("select * from main.provider_state order by id"))
        import app.provider_state_schema_release as release

        def fail_compare(*_args):
            raise RuntimeError("injected inverse verification failure")

        monkeypatch.setattr(release, "_same_facts", fail_compare)
        db.execute("BEGIN")
        with pytest.raises(RuntimeError, match="inverse verification"):
            rollback_provider_state_migration(db, metadata)
        assert db.in_transaction
        db.rollback()
        assert _schema(db) == before
        assert list(db.execute("select * from main.provider_state order by id")) == target_rows


def test_external_dependency_and_caller_rollback_after_midcopy_failure(monkeypatch):
    with source_db("actual") as db:
        db.execute(f"create view provider_view as select state from main.{OBS}")
        before = _schema(db)
        with pytest.raises(ValueError):
            capture_provider_state_source(db)
        assert _schema(db) == before
    with source_db("actual") as db:
        _seed(db)
        metadata = capture_provider_state_source(db)
        before_schema, before_facts = _schema(db), _source_facts(db)
        db.commit()
        import app.provider_state_schema_release as release
        original = release._observation_bind
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected mid-copy failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(release, "_observation_bind", fail_second)
        db.execute("BEGIN")
        with pytest.raises(RuntimeError, match="mid-copy"):
            apply_provider_state_migration(db, metadata)
        assert db.in_transaction
        db.rollback()
        assert _schema(db) == before_schema
        assert _source_facts(db) == before_facts
