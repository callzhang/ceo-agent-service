"""Published Phase 5E source preflight; no target migration is exercised here."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app import action_storage
from app.action_reply_storage import capture_reply_source
from app.store import AutoReplyStore


SOURCE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                     "2026-10-13-database-redesign-phase5e-reply-source.json").read_text())


def _database(path, variant, *, missing_column=False):
    db = sqlite3.connect(path)
    db.execute("pragma foreign_keys=on")
    db.execute("create table service_state(key text primary key,value text not null,"
               "updated_at text not null default current_timestamp)")
    db.execute("insert into service_state(key,value) values('store_schema_version','2026-10-13.4')")
    db.execute("create table email_schema_migrations(version integer primary key,applied_at text not null)")
    db.execute("insert into email_schema_migrations values(51,'source')")
    db.execute("create table tasks(id integer primary key,record_type text not null)")
    db.execute("create table runs(id integer primary key,record_type text not null)")
    action_storage.create_action_schema(db, include_delivery=True)
    for table in ("reply_attempts", "memory_write_events"):
        definition = SOURCE[variant]["tables"][table]
        ddl = definition["sql"]
        if missing_column and table == "reply_attempts":
            ddl = ddl.replace("trigger_sender text not null,\n", "")
        db.execute(ddl)
        for index in reversed(definition["indexes"]):
            if index["sql"] is not None:
                db.execute(index["sql"])
        for trigger in definition["triggers"]:
            db.execute(trigger["sql"])
    db.commit()
    return db


def _snapshot(path):
    with open(path, "rb") as file:
        file_bytes = file.read()
    with closing(sqlite3.connect(path)) as db:
        return file_bytes, list(db.execute("select type,name,tbl_name,sql from sqlite_schema order by type,name")), \
            list(db.execute("select name,seq from sqlite_sequence order by name"))


@pytest.mark.parametrize("variant", ("actual", "fresh_candidate17"))
def test_exact_published_source_variants_are_captured(tmp_path, variant):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, variant)) as db:
        db.row_factory = sqlite3.Row
        captured = capture_reply_source(db)
        assert captured["variant"] == variant
        assert captured["store_version"] == "2026-10-13.4"
        assert captured["email_version"] == 51
        assert captured["counts"] == {"reply_attempts": 0, "memory_write_events": 0}


def test_current_fresh_store_constructor_matches_frozen_fresh_source(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    AutoReplyStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("create table email_schema_migrations(version integer primary key,applied_at text not null)")
        db.execute("insert into email_schema_migrations values(51,'source')")
        assert capture_reply_source(db)["variant"] == "fresh_candidate17"


@pytest.mark.parametrize("change", (
    "unknown_marker", "mixed_old_target", "missing_column", "extra_trigger",
    "extra_index", "external_fk", "external_view", "wrong_actions",
))
def test_unsupported_source_is_rejected_before_mutation(tmp_path, change):
    path = tmp_path / "source.sqlite3"
    with closing(_database(path, "actual", missing_column=change == "missing_column")) as db:
        if change == "unknown_marker":
            db.execute("update service_state set value='unknown' where key='store_schema_version'")
        elif change == "mixed_old_target":
            db.execute("insert into sqlite_sequence(name,seq) values('actions:reply_attempts',1)")
        elif change == "extra_trigger":
            db.execute("create trigger extra_attempt after insert on reply_attempts begin select 1; end")
        elif change == "extra_index":
            db.execute("create index extra_attempt on reply_attempts(id)")
        elif change == "external_fk":
            db.execute("create table outside(id integer primary key,attempt_id integer references reply_attempts(id))")
        elif change == "external_view":
            db.execute("create view outside as select id from reply_attempts")
        elif change == "wrong_actions":
            db.execute("create index extra_actions on actions(id)")
        db.commit()
    before = _snapshot(path)
    with closing(sqlite3.connect(path)) as db:
        with pytest.raises(ValueError, match="unsupported"):
            capture_reply_source(db)
        assert not db.in_transaction
    assert _snapshot(path) == before
