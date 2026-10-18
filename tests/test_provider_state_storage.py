"""Independent literal-source controls for the unselected provider-state unit."""

from contextlib import closing, contextmanager
import json
import sqlite3

import pytest

from app.provider_state_definitions import OBSERVATION_FIELDS, GENERATION_FIELDS
from app.provider_state_storage import (
    create_provider_state_schema, get_observation, get_folder_generation,
    insert_observation, insert_folder_generation, delete_observation,
    delete_folder_generation, upsert_observations, upsert_folder_generations,
    mark_authoritative_missing, mark_unavailable_folder, mark_inactive_accounts,
    update_important_signals,
)
from test_provider_state_source import source_db, OBS, GEN, T1, T2


def test_target_declaration():
    with closing(sqlite3.connect(":memory:")) as db:
        assert_target_declaration(db)


def test_source_shaped_rows_use_fewer_table_pages_without_payload_keys():
    with closing(sqlite3.connect(":memory:")) as db:
        create_provider_state_schema(db)
        db.execute("""
            CREATE TABLE old_provider_layout (
                id INTEGER PRIMARY KEY, record_type TEXT, account_id TEXT,
                stable_message_identity TEXT, state TEXT, provider_folder_id TEXT,
                observed_at TEXT, payload_json TEXT)
        """)
        for name, columns, kind, unique in (
            ("observation_identity", "account_id,stable_message_identity", "observation", "UNIQUE "),
            ("generation_identity", "account_id,provider_folder_id", "folder_generation", "UNIQUE "),
            ("observation_folder", "account_id,provider_folder_id", "observation", ""),
        ):
            db.execute(f"CREATE {unique}INDEX old_{name} ON old_provider_layout({columns}) "
                       f"WHERE record_type='{kind}'")
        rows = [observation(stable_message_identity=f"message-{n:06d}",
                            provider_folder_name="Inbox", category_key="work",
                            important_signals_json='["flagged"]') for n in range(4096)]
        db.execute("BEGIN IMMEDIATE")
        for row in rows:
            insert_observation(db, row)
            db.execute(
                "INSERT INTO old_provider_layout(record_type,account_id,stable_message_identity,"
                "state,provider_folder_id,observed_at,payload_json) VALUES('observation',?,?,?,?,?,?)",
                (row["account_id"], row["stable_message_identity"], row["state"],
                 row["provider_folder_id"], row["observed_at"], json.dumps({
                    name: row[name] for name in (
                        "provider_folder_name", "category_key", "important", "important_signals_json"
                    )}, separators=(",", ":"))),
            )
        pages = dict(db.execute("SELECT name,sum(pgsize) FROM dbstat "
                                "WHERE name IN ('provider_state','old_provider_layout') GROUP BY name"))
        assert pages["provider_state"] < pages["old_provider_layout"]
        assert db.execute("SELECT count(*) FROM provider_state").fetchone() == (len(rows),)
        assert db.execute("SELECT count(*) FROM old_provider_layout").fetchone() == (len(rows),)


def assert_target_declaration(db):
    create_provider_state_schema(db)
    columns = {row[1]: row for row in db.execute("pragma table_xinfo(provider_state)")}
    assert columns["id"][2:7] == ("INTEGER", 0, None, 1, 0)
    assert {name for name, row in columns.items() if row[6] == 0} >= {
        "provider_folder_name", "category_key", "important", "important_signals_json"
    }
    assert "payload_json" not in columns
    assert all(row[6] == 0 for row in columns.values())
    assert {name: columns[name][2] for name in (
        "provider_folder_name", "category_key", "important", "important_signals_json"
    )} == {
        "provider_folder_name": "TEXT", "category_key": "TEXT",
        "important": "INTEGER", "important_signals_json": "TEXT",
    }
    assert {row[1] for row in db.execute("pragma index_list(provider_state)")} == {
        "idx_provider_state_observation_identity",
        "idx_provider_state_generation_identity",
        "idx_provider_state_observation_folder",
    }
    assert list(db.execute("pragma foreign_key_list(provider_state)")) == []
    assert not list(db.execute("select name from sqlite_master where type='trigger' and tbl_name='provider_state'"))
    assert "AUTOINCREMENT" not in db.execute(
        "select sql from sqlite_master where name='provider_state'"
    ).fetchone()[0].upper()
    expected_indexes = {
        "observation": ("stable_message_identity", "idx_provider_state_observation_identity"),
        "folder_generation": ("provider_folder_id", "idx_provider_state_generation_identity"),
    }
    for kind, (key, index) in expected_indexes.items():
        plan = list(db.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM provider_state WHERE record_type='" + kind +
            "' AND account_id=? AND " + key + "=?", ("a", "m"),
        ))
        assert index in plan[0][3]
    plan = list(db.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM provider_state WHERE record_type='observation' "
        "AND account_id=? AND provider_folder_id=?", ("a", "inbox"),
    ))
    assert "idx_provider_state_observation_folder" in plan[0][3]
    for index in db.execute("pragma index_list(provider_state)"):
        assert index[4] == 1
        keys = [row for row in db.execute(f"pragma index_xinfo({index[1]})") if row[5] == 1]
        assert all(row[4] == "BINARY" for row in keys)
    db.execute("BEGIN IMMEDIATE")
    insert_folder_generation(db, {"account_id": "a", "provider_folder_id": "inbox", "observed_at": T1})
    assert db.execute(
        "SELECT provider_folder_name,category_key,important,important_signals_json "
        "FROM provider_state WHERE record_type='folder_generation'"
    ).fetchone() == (None, None, None, None)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO provider_state(record_type,account_id,provider_folder_id,"
                   "observed_at,provider_folder_name) VALUES('folder_generation','a','other',?,'Other')", (T1,))
    for field, value in (("important", 2), ("important_signals_json", "{bad")):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO provider_state(record_type,account_id,stable_message_identity,state,"
                f"observed_at,{field}) VALUES('observation','a','bad','available',?,?)",
                (T1, value),
            )

@contextmanager
def target_db():
    db = sqlite3.connect(":memory:")
    try:
        create_provider_state_schema(db)
        db.execute("BEGIN IMMEDIATE")
        yield db
    finally:
        db.close()


def source_insert(db, table, row, ignore=False):
    columns = tuple(row)
    verb = "INSERT OR IGNORE" if ignore else "INSERT"
    return db.execute(
        f"{verb} INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(row.values()),
    ).rowcount


def projected(db, table, fields, where, args):
    columns = ",".join(fields)
    classes = ",".join(f"typeof({field})" for field in fields)
    return db.execute(f"SELECT {columns},{classes} FROM {table} WHERE {where}", args).fetchone()


def observation(**changes):
    row = dict(account_id="a", stable_message_identity="m", state="available",
               provider_folder_id="inbox", provider_folder_name="Inbox",
               category_key=None, important=0, observed_at=T1,
               important_signals_json="[]")
    row.update(changes)
    return row


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_point_source_equivalence_and_native_constraints(variant):
    with source_db(variant) as source, target_db() as target:
        cases = [
            observation(important="1", important_signals_json="42", category_key=42),
            observation(stable_message_identity="M", provider_folder_id=None,
                        provider_folder_name=None, important=None, important_signals_json=None),
            observation(account_id="b"),
            observation(stable_message_identity=b"binary-key", category_key=b"binary-category"),
            observation(stable_message_identity="inf", category_key=float("inf")),
        ]
        for row in cases:
            assert source_insert(source, OBS, row) == insert_observation(target, row) == 1
            identity = (row["account_id"], row["stable_message_identity"])
            actual = projected(source, OBS, OBSERVATION_FIELDS,
                               "account_id=? AND stable_message_identity=?", identity)
            equivalent = projected(target, "provider_state", OBSERVATION_FIELDS,
                                   "record_type='observation' AND account_id=? AND stable_message_identity=?", identity)
            assert actual == equivalent
            assert get_observation(target, *identity) == dict(zip(OBSERVATION_FIELDS, actual[:9]))
        assert get_observation(target, "a", "absent") is None
        for row in (
            observation(), observation(account_id=" ", stable_message_identity="bad"),
            observation(stable_message_identity=""), observation(stable_message_identity="bad-state", state="pending"),
            observation(stable_message_identity="bad-time", observed_at=" "),
            observation(stable_message_identity="bad-importance", important=2),
            observation(stable_message_identity="bad-json", important_signals_json="{bad"),
            {"account_id": "a", "stable_message_identity": "missing-state", "observed_at": T1},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                source_insert(source, OBS, row)
            with pytest.raises(sqlite3.IntegrityError):
                insert_observation(target, row)
        duplicate = cases[0]
        assert source_insert(source, OBS, duplicate, ignore=True) == insert_observation(target, duplicate, ignore=True) == 0
        bad_ignored = observation(stable_message_identity="ignored", important=2)
        assert source_insert(source, OBS, bad_ignored, ignore=True) == insert_observation(
            target, bad_ignored, ignore=True
        ) == 0
        assert insert_observation(target, {
            "account_id": "a", "stable_message_identity": "optional", "state": "excluded", "observed_at": T1,
        }) == 1
        assert source_insert(source, OBS, {
            "account_id": "a", "stable_message_identity": "optional", "state": "excluded", "observed_at": T1,
        }) == 1
        assert projected(source, OBS, OBSERVATION_FIELDS,
                         "account_id='a' AND stable_message_identity='optional'", ()) == projected(
            target, "provider_state", OBSERVATION_FIELDS,
            "record_type='observation' AND account_id='a' AND stable_message_identity='optional'", ()
        )
        assert get_observation(target, "a", "optional") == observation(
            stable_message_identity="optional", state="excluded", provider_folder_id=None,
            provider_folder_name=None, important=None, important_signals_json=None,
        )
        for row in (
            dict(account_id="a", provider_folder_id="inbox", observed_at=T1),
            dict(account_id="a", provider_folder_id="INBOX", observed_at=T1),
            dict(account_id="b", provider_folder_id="inbox", observed_at=T1),
        ):
            assert source_insert(source, GEN, row) == insert_folder_generation(target, row) == 1
            key = (row["account_id"], row["provider_folder_id"])
            actual = projected(source, GEN, GENERATION_FIELDS,
                               "account_id=? AND provider_folder_id=?", key)
            assert actual == projected(target, "provider_state", GENERATION_FIELDS,
                                       "record_type='folder_generation' AND account_id=? AND provider_folder_id=?", key)
            assert get_folder_generation(target, *key) == row
        assert get_folder_generation(target, "a", "absent") is None
        with pytest.raises(sqlite3.IntegrityError):
            insert_folder_generation(target, dict(account_id="a", provider_folder_id="inbox", observed_at=T2))
        assert insert_folder_generation(target, dict(account_id="a", provider_folder_id="inbox", observed_at=T2), ignore=True) == 0
        assert delete_observation(target, "a", "m") == 1
        assert delete_observation(target, "a", "m") == 0
        assert insert_observation(target, cases[0]) == 1
        assert delete_folder_generation(target, "a", "inbox") == 1
        assert delete_folder_generation(target, "a", "inbox") == 0
        assert insert_folder_generation(target, dict(account_id="a", provider_folder_id="inbox", observed_at=T2)) == 1


def test_writes_require_caller_transaction():
    db = sqlite3.connect(":memory:")
    create_provider_state_schema(db)
    with pytest.raises(sqlite3.ProgrammingError):
        insert_observation(db, observation())
    with pytest.raises(sqlite3.ProgrammingError):
        insert_folder_generation(db, dict(account_id="a", provider_folder_id="inbox", observed_at=T1))
    calls = (
        lambda: upsert_observations(db, [observation()]),
        lambda: upsert_folder_generations(db, [dict(account_id="a", provider_folder_id="inbox", observed_at=T1)]),
        lambda: mark_authoritative_missing(db, "a", "inbox", T2),
        lambda: mark_unavailable_folder(db, "a", "inbox", T2),
        lambda: mark_inactive_accounts(db, T2),
        lambda: update_important_signals(db, "a", "m", 1, "[]"),
        lambda: delete_observation(db, "a", "m"),
        lambda: delete_folder_generation(db, "a", "inbox"),
    )
    for call in calls:
        with pytest.raises(sqlite3.ProgrammingError):
            call()
    assert db.execute("SELECT count(*) FROM provider_state").fetchone()[0] == 0


def test_scan_upsert_requires_full_named_source_row():
    with target_db() as db:
        incomplete = {"account_id": "a", "stable_message_identity": "m",
                      "state": "available", "observed_at": T1}
        with pytest.raises(sqlite3.ProgrammingError):
            upsert_observations(db, [incomplete])
        assert db.execute("SELECT count(*) FROM provider_state").fetchone()[0] == 0

def all_facts(db, table, fields, predicate="1"):
    columns = ",".join(fields)
    classes = ",".join(f"typeof({field})" for field in fields)
    return sorted(db.execute(
        f"SELECT {columns},{classes} FROM {table} WHERE {predicate}").fetchall(), key=repr)


def assert_same_facts(source, target):
    assert all_facts(source, OBS, OBSERVATION_FIELDS) == all_facts(
        target, "provider_state", OBSERVATION_FIELDS, "record_type='observation'"
    )
    assert all_facts(source, GEN, GENERATION_FIELDS) == all_facts(
        target, "provider_state", GENERATION_FIELDS, "record_type='folder_generation'"
    )


def source_upsert_observations(db, rows):
    columns = tuple(OBSERVATION_FIELDS)
    statement = (
        f"INSERT INTO {OBS} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
        "ON CONFLICT(account_id,stable_message_identity) DO UPDATE SET "
        "state=excluded.state,provider_folder_id=excluded.provider_folder_id,"
        "provider_folder_name=excluded.provider_folder_name,category_key=excluded.category_key,"
        "important=excluded.important,important_signals_json=excluded.important_signals_json,"
        "observed_at=excluded.observed_at WHERE "
        + " OR ".join(f"{OBS}.{field} IS NOT excluded.{field}" for field in (
            "state", "provider_folder_id", "provider_folder_name", "category_key",
            "important", "important_signals_json",
        ))
    )
    db.executemany(statement, [tuple(row[field] for field in columns) for row in rows])


def source_upsert_generations(db, rows):
    db.executemany(
        f"INSERT INTO {GEN}(account_id,provider_folder_id,observed_at) VALUES(?,?,?) "
        "ON CONFLICT(account_id,provider_folder_id) DO UPDATE SET observed_at=excluded.observed_at "
        f"WHERE {GEN}.observed_at IS NOT excluded.observed_at",
        [tuple(row[field] for field in GENERATION_FIELDS) for row in rows],
    )


def stage(db, membership=(), accounts=None):
    db.execute("CREATE TEMP TABLE current_provider_membership (account_id TEXT NOT NULL,"
               "provider_folder_id TEXT NOT NULL,stable_message_identity TEXT NOT NULL,"
               "PRIMARY KEY(account_id,provider_folder_id,stable_message_identity))")
    db.executemany("INSERT OR IGNORE INTO current_provider_membership VALUES(?,?,?)", membership)
    if accounts is not None:
        db.execute("CREATE TEMP TABLE current_provider_accounts(account_id TEXT PRIMARY KEY)")
        db.executemany("INSERT OR IGNORE INTO current_provider_accounts VALUES(?)", ((a,) for a in accounts))


def source_authoritative_missing(db, account_id, folder_id, when):
    return db.execute(
        f"UPDATE {OBS} SET state='unavailable',provider_folder_id=NULL,provider_folder_name=NULL,"
        "category_key=NULL,important=NULL,important_signals_json=NULL,observed_at=? "
        "WHERE account_id=? AND provider_folder_id=? AND NOT EXISTS ("
        "SELECT 1 FROM current_provider_membership m WHERE m.account_id="
        f"{OBS}.account_id AND m.provider_folder_id=? AND "
        f"m.stable_message_identity={OBS}.stable_message_identity) AND ("
        "state IS NOT 'unavailable' OR provider_folder_id IS NOT NULL OR "
        "provider_folder_name IS NOT NULL OR category_key IS NOT NULL OR important IS NOT NULL "
        "OR important_signals_json IS NOT NULL OR observed_at IS NOT ?)",
        (when, account_id, folder_id, folder_id, when),
    ).rowcount


def source_unavailable_folder(db, account_id, folder_id, when):
    return db.execute(
        f"UPDATE {OBS} SET state='unavailable',category_key=NULL,important=NULL,"
        "important_signals_json=NULL,observed_at=? WHERE account_id=? AND provider_folder_id=? "
        "AND (state IS NOT 'unavailable' OR category_key IS NOT NULL OR important IS NOT NULL "
        "OR important_signals_json IS NOT NULL OR observed_at IS NOT ?)",
        (when, account_id, folder_id, when),
    ).rowcount


def source_inactive_accounts(db, when):
    return db.execute(
        f"UPDATE {OBS} SET state='unavailable',provider_folder_id=NULL,provider_folder_name=NULL,"
        "category_key=NULL,important=NULL,important_signals_json=NULL,observed_at=? "
        "WHERE NOT EXISTS (SELECT 1 FROM current_provider_accounts a WHERE a.account_id="
        f"{OBS}.account_id) AND (state IS NOT 'unavailable' OR provider_folder_id IS NOT NULL "
        "OR provider_folder_name IS NOT NULL OR category_key IS NOT NULL OR important IS NOT NULL "
        "OR important_signals_json IS NOT NULL OR observed_at IS NOT ?)",
        (when, when),
    ).rowcount


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_scan_lifecycle_matches_literal_source(variant):
    with source_db(variant) as source, target_db() as target:
        first = [observation(), observation(stable_message_identity="n"),
                 observation(account_id="b", stable_message_identity="z", provider_folder_id="archive",
                             provider_folder_name="Archive", category_key="work")]
        source_upsert_observations(source, first)
        upsert_observations(target, first)
        assert_same_facts(source, target)  # facts precede classification
        original_id = target.execute(
            "SELECT id FROM provider_state WHERE record_type='observation' "
            "AND account_id='a' AND stable_message_identity='m'"
        ).fetchone()[0]
        generations = [dict(account_id="a", provider_folder_id="inbox", observed_at=T1),
                       dict(account_id="b", provider_folder_id="archive", observed_at=T1)]
        source_upsert_generations(source, generations)
        upsert_folder_generations(target, generations)
        assert_same_facts(source, target)
        changed_encoding = [observation(important="1", category_key=42, observed_at=T2),
                            observation(stable_message_identity="n", observed_at=T2)]
        source_upsert_observations(source, changed_encoding)
        upsert_observations(target, changed_encoding)
        assert_same_facts(source, target)
        assert get_observation(target, "a", "n")["observed_at"] == T1
        assert get_observation(target, "a", "m")["observed_at"] == T2
        source_upsert_generations(source, [dict(account_id="a", provider_folder_id="inbox", observed_at=T2)])
        upsert_folder_generations(target, [dict(account_id="a", provider_folder_id="inbox", observed_at=T2)])
        assert_same_facts(source, target)
        assert get_observation(target, "a", "n")["observed_at"] == T1
        equivalent_encoding = [observation(important=1, category_key="42", observed_at="later")]
        source_upsert_observations(source, equivalent_encoding)
        upsert_observations(target, equivalent_encoding)
        assert_same_facts(source, target)
        assert get_observation(target, "a", "m")["observed_at"] == T2
        moved = [observation(stable_message_identity="m", provider_folder_id="archive",
                             provider_folder_name="Archive", observed_at="move")]
        source_upsert_observations(source, moved)
        upsert_observations(target, moved)
        assert_same_facts(source, target)
        assert target.execute(
            "SELECT id FROM provider_state WHERE record_type='observation' "
            "AND account_id='a' AND stable_message_identity='m'"
        ).fetchone()[0] == original_id
        stage(source, membership=(("a", "inbox", "n"),), accounts=("a", "b"))
        stage(target, membership=(("a", "inbox", "n"),), accounts=("a", "b"))
        # Partial scan: no authoritative call; omitted archive rows survive.
        assert_same_facts(source, target)
        assert source_authoritative_missing(source, "a", "inbox", "tomb") == mark_authoritative_missing(target, "a", "inbox", "tomb") == 0
        assert source_authoritative_missing(source, "a", "archive", "tomb") == mark_authoritative_missing(target, "a", "archive", "tomb") == 1
        assert_same_facts(source, target)
        assert source_unavailable_folder(source, "b", "archive", "unavailable") == mark_unavailable_folder(target, "b", "archive", "unavailable") == 1
        assert_same_facts(source, target)
        assert get_observation(target, "b", "z")["provider_folder_name"] == "Archive"
        source_upsert_generations(source, [dict(account_id="b", provider_folder_id="archive", observed_at="generation")])
        upsert_folder_generations(target, [dict(account_id="b", provider_folder_id="archive", observed_at="generation")])
        assert_same_facts(source, target)
        assert source.execute(
            f"SELECT coalesce(g.observed_at,o.observed_at) FROM {OBS} o LEFT JOIN {GEN} g "
            "ON g.account_id=o.account_id AND g.provider_folder_id=o.provider_folder_id "
            "WHERE o.account_id='b' AND o.stable_message_identity='z'"
        ).fetchone()[0] == target.execute(
            "SELECT coalesce(g.observed_at,o.observed_at) FROM provider_state o "
            "LEFT JOIN provider_state g ON g.record_type='folder_generation' AND "
            "g.account_id=o.account_id AND g.provider_folder_id=o.provider_folder_id "
            "WHERE o.record_type='observation' AND o.account_id='b' AND o.stable_message_identity='z'"
        ).fetchone()[0] == "generation"
        assert source.execute(
            f"UPDATE {OBS} SET important=1,important_signals_json='[\"star\"]' "
            "WHERE account_id='a' AND stable_message_identity='n' AND state='available'"
        ).rowcount == 1
        assert update_important_signals(target, "a", "n", 1, '["star"]') is True
        assert update_important_signals(target, "b", "z", 1, '["star"]') is False
        assert_same_facts(source, target)
        assert get_observation(target, "a", "n")["observed_at"] == T1
        assert get_folder_generation(target, "a", "inbox")["observed_at"] == T2
        # None means no inactive-account call; explicit empty membership invalidates all.
        source.execute("DELETE FROM current_provider_accounts")
        target.execute("DELETE FROM current_provider_accounts")
        assert source_inactive_accounts(source, "inactive") == mark_inactive_accounts(target, "inactive")
        assert_same_facts(source, target)


@pytest.mark.parametrize("variant", ("actual", "fresh"))
def test_failed_generation_rolls_back_observation_group(variant):
    with source_db(variant) as source, target_db() as target:
        original = observation()
        source_insert(source, OBS, original)
        insert_observation(target, original)
        source.commit()
        target.commit()
        for db in (source, target):
            db.execute("BEGIN IMMEDIATE")
        changed = observation(state="excluded", observed_at=T2)
        source_upsert_observations(source, [changed])
        upsert_observations(target, [changed])
        with pytest.raises(sqlite3.IntegrityError):
            source_upsert_generations(source, [dict(account_id="a", provider_folder_id="", observed_at=T2)])
        with pytest.raises(sqlite3.IntegrityError):
            upsert_folder_generations(target, [dict(account_id="a", provider_folder_id="", observed_at=T2)])
        source.rollback()
        target.rollback()
        assert_same_facts(source, target)
        assert get_observation(target, "a", "m")["state"] == "available"
