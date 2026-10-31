"""Template copies preserve constructor options and independent databases."""
from contextlib import closing
import sqlite3

from tests.support import store_template


def test_keyword_constructor_options_reuse_schema_but_still_run(tmp_path, monkeypatch):
    monkeypatch.setattr(store_template, "_TEMPLATES", {})
    monkeypatch.setattr(store_template, "_DIRECTORY", tmp_path / "templates")
    monkeypatch.setattr(store_template, "_BUILDING", False)
    (tmp_path / "templates").mkdir()
    created = []

    class Store:
        def __init__(self, path, *, option=True):
            with closing(sqlite3.connect(path)) as db:
                exists = db.execute(
                    "select 1 from sqlite_schema where name='settings'"
                ).fetchone()
                if not exists:
                    created.append(path)
                    db.execute("create table settings(value integer)")
                    db.execute("insert into settings values (1)")
                db.execute("update settings set value=?", (int(option),))
                db.commit()

    store_template.install(Store, "test-keywords")
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    Store(first, option=False)
    Store(second, option=True)
    with closing(sqlite3.connect(first)) as db:
        assert db.execute("select value from settings").fetchone()[0] == 0
    with closing(sqlite3.connect(second)) as db:
        assert db.execute("select value from settings").fetchone()[0] == 1
    assert len(created) == 1
    assert created[0].parent == tmp_path / "templates"
