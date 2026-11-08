"""Template copies preserve constructor options and independent databases."""

from contextlib import closing
import sqlite3

import pytest

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


def test_initialized_empty_clones_validate_without_repeating_bootstrap(
    tmp_path, monkeypatch
):
    from app import store as store_module
    from app.store import AutoReplyStore

    monkeypatch.setattr(store_template, "_TEMPLATES", {})
    monkeypatch.setattr(store_template, "_DIRECTORY", tmp_path / "templates")
    monkeypatch.setattr(store_template, "_BUILDING", False)
    (tmp_path / "templates").mkdir()
    bootstraps = []
    checks = []
    original_bootstrap = AutoReplyStore._ensure_wal_journal_mode
    original_check = store_module.require_current_pair

    def bootstrap(self):
        bootstraps.append(self.path)
        return original_bootstrap(self)

    def checked(path):
        checks.append(path)
        return original_check(path)

    monkeypatch.setattr(AutoReplyStore, "_ensure_wal_journal_mode", bootstrap)
    monkeypatch.setattr(store_module, "require_current_pair", checked)
    first = AutoReplyStore(tmp_path / "first.sqlite3")
    second = AutoReplyStore(tmp_path / "second.sqlite3")
    first.set_service_state("independent", "first only")
    assert second.get_service_state("independent") is None
    assert first.path in checks and second.path in checks
    assert bootstraps == [store_template._TEMPLATES["store"]]


@pytest.mark.parametrize("corruption", ["version", "schema"])
def test_copied_initialized_corruption_still_rejected(
    tmp_path, monkeypatch, corruption
):
    from app.store import AutoReplyStore, _close_idle_connections

    monkeypatch.setattr(store_template, "_TEMPLATES", {})
    monkeypatch.setattr(store_template, "_DIRECTORY", tmp_path / "templates")
    monkeypatch.setattr(store_template, "_BUILDING", False)
    (tmp_path / "templates").mkdir()
    AutoReplyStore(tmp_path / "first.sqlite3")
    template = store_template._TEMPLATES["store"]
    if corruption == "version":
        AutoReplyStore(template).set_service_state(
            "store_schema_version", "invalid version"
        )
        expected = "unsupported Store version"
    else:
        with closing(sqlite3.connect(template)) as db:
            db.execute("create index invalid_extra_index on state(record_type)")
        expected = "unsupported or malformed"
    _close_idle_connections()
    with pytest.raises(sqlite3.IntegrityError, match=expected):
        AutoReplyStore(tmp_path / "corrupted.sqlite3")
