import sqlite3

from tools.native_copy_validation import _identities, fact_digest


def test_original_cohort_excludes_new_rows_filling_old_rowid_holes(tmp_path):
    path = tmp_path / "cohort.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table facts(id integer primary key, value blob)")
        db.executemany("insert into facts values(?,?)", ((1, "one"), (100, b"hundred")))
    identities = _identities(path)
    before = fact_digest(path, original_identities=identities)
    with sqlite3.connect(path) as db:
        db.execute("insert into facts values(2,'new native row in an existing hole')")
    assert fact_digest(path, original_identities=identities) == before
    assert fact_digest(path) != before
    with sqlite3.connect(path) as db:
        db.execute("update facts set value='hundred' where id=100")
    assert fact_digest(path, original_identities=identities) != before


def test_original_cohort_preserves_composite_without_rowid_identity(tmp_path):
    path = tmp_path / "composite.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("create table facts(kind text,n integer,value blob,primary key(kind,n)) without rowid")
        db.executemany("insert into facts values(?,?,?)", (("a", 1, "one"), ("z", 9, b"last")))
    identities = _identities(path)
    before = fact_digest(path, original_identities=identities)
    with sqlite3.connect(path) as db:
        db.execute("insert into facts values('m',2,'new')")
    assert fact_digest(path, original_identities=identities) == before
    with sqlite3.connect(path) as db:
        db.execute("delete from facts where kind='a' and n=1")
    assert fact_digest(path, original_identities=identities) != before
