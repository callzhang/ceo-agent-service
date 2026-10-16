"""Prevent false source-preservation acceptance in the copied-DB proof."""
from contextlib import closing
import sqlite3

import pytest

from scripts.validate_state_runtime_copy import inventory


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            create table allocator(id integer primary key autoincrement);
            create table service_state(key text primary key,value);
            insert into service_state(rowid,key,value) values(-7,'original',x'37');
            insert into service_state(rowid,key,value) values(0,null,'original-null');
            create table email_scan_cursors(account_id text,folder text,last_seen_uid,
                                             primary key(account_id,folder));
            insert into email_scan_cursors(rowid,account_id,folder,last_seen_uid)
                values(-7,'original-account','original-folder',7);
        """)
        db.commit()
    return path


def test_exact_witnesses_preserve_original_null_and_signed_rowid_rows(source):
    before = inventory(source)
    assert all(value == before["facts"][family]
               for family, value in before["original_witness_facts"].items())
    assert before["original_witness_facts"]["service_state"][0] == 2
    assert before["original_witness_facts"]["email_scan_cursors"][0] == 1
    with closing(sqlite3.connect(source)) as db:
        db.executescript("""
            insert into service_state(key,value) values('state_copy_current','current');
            insert into service_state(key,value) values('state_copy_predecessor','predecessor');
            insert into email_scan_cursors(account_id,folder,last_seen_uid)
                values('copy-only-state-account','copy-only-state-folder',11);
        """)
        db.commit()
    after = inventory(source)
    assert after["original_witness_facts"] == before["original_witness_facts"]
    assert after["facts"]["service_state"][0] == 4
    assert after["facts"]["email_scan_cursors"][0] == 2


@pytest.mark.parametrize("family,sql", (
    ("service_state", "update service_state set value='7' where key='original'"),
    ("service_state", "delete from service_state where key='original'"),
    ("service_state", "insert into service_state(key,value) values('unowned','new')"),
    ("service_state", "update service_state set rowid=99 where key='original'"),
    ("service_state", "update service_state set value='changed' where key is null"),
    ("service_state", "insert into service_state(key,value) values(null,'extra-null')"),
    ("email_scan_cursors", "update email_scan_cursors set last_seen_uid=0"),
    ("email_scan_cursors", "delete from email_scan_cursors"),
    ("email_scan_cursors", "update email_scan_cursors set rowid=99"),
    ("email_scan_cursors", "update email_scan_cursors set last_seen_uid=x'37'"),
    ("email_scan_cursors", "insert into email_scan_cursors values(null,'copy-only-state-folder',1)"),
    ("email_scan_cursors", "insert into email_scan_cursors values('copy-only-state-account',null,1)"),
    ("email_scan_cursors", "insert into email_scan_cursors values('copy-only-state-account','other-folder',1)"),
    ("email_scan_cursors", "insert into email_scan_cursors values('other-account','copy-only-state-folder',1)"),
))
def test_original_row_corruption_is_detected_inside_witness_families(source, family, sql):
    before = inventory(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute(sql)
        db.commit()
    after = inventory(source)
    assert after["original_witness_facts"][family] != before["original_witness_facts"][family]


@pytest.mark.parametrize("family", (
    "email_unsubscribe_effects", "email_provider_observations",
    "email_provider_folder_observation_generations",
))
def test_composite_business_identity_ignores_only_incidental_rowid(source, family):
    with closing(sqlite3.connect(source)) as db:
        db.execute(f"create table {family}(a text,b text,value,primary key(a,b))")
        db.execute(f"insert into {family}(rowid,a,b,value) values(-7,'a','b',x'37')")
        db.commit()
    before = inventory(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute(f"update {family} set rowid=99")
        db.commit()
    assert inventory(source)["facts"][family] == before["facts"][family]
    with closing(sqlite3.connect(source)) as db:
        db.execute(f"update {family} set value='7'")
        db.commit()
    assert inventory(source)["facts"][family] != before["facts"][family]
