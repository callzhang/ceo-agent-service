"""Matched Phase3 inverse restores schema without replacing business writes."""

from contextlib import closing
import sqlite3
import json

import pytest

from app.store import AutoReplyStore
from app.repository_updater import RepositoryUpdater, UpgradeFailed
from tests.test_repository_updater_publication import _State, _repo
from app.store_schema_release import (
    INPUT_VERSION,
    VERSION_KEY,
    prepare_store_schema_release,
)

TASK_DDL = "CREATE TABLE \"reply_tasks\" (\n                    id integer primary key autoincrement,\n                    channel text not null default 'dingtalk',\n                    conversation_id text not null,\n                    conversation_title text not null,\n                    single_chat integer not null,\n                    trigger_message_id text not null,\n                    trigger_create_time text not null,\n                    trigger_sender text not null,\n                    trigger_text text not null,\n                    trigger_message_json text not null default '{}',\n                    available_at text not null default '',\n                    force_new_decision integer not null default 0,\n                    oa_url text not null default '',\n                    manual_rerun_attempt_id integer not null default 0,\n                    status text not null default 'pending',\n                    attempts integer not null default 0,\n                    locked_at text,\n                    error text not null default '',\n                    created_at text not null default current_timestamp,\n                    updated_at text not null default current_timestamp, execution_generation text not null default 'initial', manual_rerun_revision_key text not null default '', recovery_code text not null default '', business_object_key text not null default '', input_version integer not null default 1, claimed_input_version integer not null default 0, input_provenance_json text not null default '{}', input_compacted integer not null default 0,\n                    unique(channel, conversation_id, trigger_message_id)\n                )"
INPUT_DDL = "CREATE TABLE \"reply_task_inputs\" (\n                    id integer primary key autoincrement,\n                    reply_task_id integer not null,\n                    channel text not null,\n                    conversation_id text not null,\n                    conversation_title text not null,\n                    single_chat integer not null,\n                    trigger_message_id text not null,\n                    trigger_create_time text not null,\n                    trigger_sender text not null,\n                    trigger_text text not null,\n                    trigger_message_json text not null default '{}',\n                    oa_url text not null default '',\n                    business_object_key text not null,\n                    input_revision_key text not null default '',\n                    created_at text not null default current_timestamp, input_provenance_json text not null default '{}', input_compacted integer not null default 0,\n                    unique(\n                        channel, conversation_id, trigger_message_id,\n                        input_revision_key\n                    ),\n                    foreign key(reply_task_id) references reply_tasks(id)\n                )"
INDEX_DDL = (
    "CREATE INDEX idx_reply_tasks_status on reply_tasks(status, id)",
    "CREATE INDEX idx_reply_tasks_channel_status_id\n                    on reply_tasks(channel, status, id)\n                ",
    "CREATE INDEX idx_reply_tasks_business_object_key on reply_tasks(business_object_key)",
    "CREATE INDEX idx_reply_tasks_email_unsubscribe_status\n                    on reply_tasks(updated_at desc, id desc, status)\n                    where channel='email'\n                      and json_valid(trigger_message_json)\n                      and json_extract(trigger_message_json, '$.schema')=\n                          'email_agent_action.v1'\n                      and json_extract(trigger_message_json, '$.lifecycle_version')=\n                          'email_unsubscribe_audited_v2'\n                      and json_extract(trigger_message_json, '$.action_type')=\n                          'unsubscribe'\n                ",
)


def legacy_database(tmp_path, *, mismatch=False):
    root = tmp_path / "target"
    (root / "app").mkdir(parents=True)
    (root / "app/store.py").write_text(f"STORE_SCHEMA_VERSION = {INPUT_VERSION!r}\n")
    path = tmp_path / "db.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(TASK_DDL)
        db.execute(INPUT_DDL)
        for sql in INDEX_DDL:
            db.execute(sql)
        db.execute(
            "create table service_state(key text primary key,value text,updated_at text)"
        )
        db.execute(
            "insert into service_state values(?,?,?)",
            (VERSION_KEY, "2026-10-10.2", "original-time"),
        )
        db.execute(
            "create table execution_receipts(id integer primary key,reply_task_id integer references reply_tasks(id),result text)"
        )
        db.execute(
            "insert into reply_tasks(conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,trigger_text,trigger_message_json,status,input_version,claimed_input_version) values('chat','Chat',0,'m','time','sender','current','{}','done',7,7)"
        )
        db.execute(
            "insert into reply_task_inputs(reply_task_id,channel,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,trigger_text,trigger_message_json,business_object_key) values(1,'dingtalk','chat','Chat',0,'m','time','sender',?,'{}','')",
            ("old" if mismatch else "current",),
        )
        db.execute("insert into execution_receipts values(1,1,'original')")
    return root, path


def migrate(root, path):
    with closing(sqlite3.connect(path)) as db, db:
        db.row_factory = sqlite3.Row
        AutoReplyStore._migrate_reply_task_input_references(db)
        db.execute(
            "update service_state set value=?,updated_at=? where key=?",
            (INPUT_VERSION, "new-time", VERSION_KEY),
        )


def test_inverse_keeps_new_input_state_and_execution_receipt(tmp_path):
    root, path = legacy_database(tmp_path, mismatch=True)
    publication = prepare_store_schema_release(root, path)
    publication.migrate = migrate
    publication.publish()
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("select count(*) from reply_task_inputs").fetchone() == (2,)
        assert db.execute(
            "select input_version,claimed_input_version from reply_tasks"
        ).fetchone() == (7, 7)
        db.execute(
            "insert into reply_task_inputs(reply_task_id,channel,conversation_id,conversation_title,single_chat,trigger_message_id,trigger_create_time,trigger_sender,trigger_text,trigger_message_json,business_object_key,input_revision_key) values(1,'dingtalk','chat','Chat',0,'m','time','sender','newest','{}','','new')"
        )
        db.execute(
            "update reply_tasks set current_input_id=3,status='needs_human',input_version=8 where id=1"
        )
        db.execute("insert into execution_receipts values(2,1,'post-start')")
    publication.rollback()
    publication.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute(
            "select trigger_text,status,input_version,claimed_input_version from reply_tasks"
        ).fetchone() == ("newest", "needs_human", 8, 7)
        assert db.execute(
            "select value,updated_at from service_state where key=?", (VERSION_KEY,)
        ).fetchone() == ("2026-10-10.2", "original-time")
        assert db.execute(
            "select * from execution_receipts order by id"
        ).fetchall() == [(1, 1, "original"), (2, 1, "post-start")]
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert db.execute("pragma quick_check").fetchone() == ("ok",)


def test_inverse_failure_is_atomic(tmp_path, monkeypatch):
    root, path = legacy_database(tmp_path)
    publication = prepare_store_schema_release(root, path)
    publication.migrate = migrate
    publication.publish()
    restore = AutoReplyStore.restore_reply_task_input_schema

    def fail(db, **kwargs):
        restore(db, **kwargs)
        raise RuntimeError("inverse interrupted")

    monkeypatch.setattr(AutoReplyStore, "restore_reply_task_input_schema", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        publication.rollback()
    publication.verify_loaded()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select count(*) from execution_receipts").fetchone() == (1,)


def test_prepare_rejects_unverified_transition(tmp_path):
    root, path = legacy_database(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("update service_state set value='future'")
    with pytest.raises(ValueError, match="verified inverse"):
        prepare_store_schema_release(root, path)


@pytest.mark.parametrize(
    "failure", ["dependencies", "verification", "restart", "health", "loaded"]
)
def test_updater_failure_restores_store_before_old_start_without_erasing_receipt(
    tmp_path, failure
):
    checkout, operation = _repo(tmp_path)
    target, path = legacy_database(tmp_path)
    publication = prepare_store_schema_release(target, path)
    publication.migrate = migrate
    state = _State()
    events = []
    starts = 0

    def prepare():
        events.append("prepare")
        return publication

    verify = publication.verify_loaded
    inverse = publication.rollback

    def verify_loaded():
        if starts and failure == "loaded":
            raise RuntimeError("loaded failure")
        verify()

    def rollback():
        events.append("inverse")
        inverse()

    publication.verify_loaded = verify_loaded
    publication.rollback = rollback

    def start():
        nonlocal starts
        starts += 1
        events.append("start")
        with closing(sqlite3.connect(path)) as db, db:
            marker = db.execute(
                "select value from service_state where key=?", (VERSION_KEY,)
            ).fetchone()[0]
            if starts == 1 and failure not in ("dependencies", "verification"):
                assert marker == INPUT_VERSION
                db.execute("insert into execution_receipts values(2,1,'post-start')")
            else:
                assert marker == "2026-10-10.2"
                assert (
                    tuple(
                        tuple(row)
                        for row in db.execute("pragma table_info(reply_tasks)")
                    )
                    == publication.original_columns
                )
        if starts == 1 and failure == "restart":
            raise RuntimeError("partial start failure")

    def fail_at(stage):
        events.append(stage)
        if failure == stage:
            raise RuntimeError(stage + " failure")

    updater = RepositoryUpdater(
        checkout,
        state,
        database_path=path,
        stop=lambda: events.append("stop"),
        restart=start,
        health=lambda: failure != "health" or starts > 1,
        database_publication=prepare,
        dependency_sync=lambda: fail_at("dependencies"),
        verification=lambda: fail_at("verification"),
    )
    with pytest.raises(UpgradeFailed):
        updater.execute(operation)
    assert events.index("prepare") < events.index("dependencies")
    assert events.index("inverse") < len(events) - 1
    if failure in ("restart", "health", "loaded"):
        assert events.count("stop") == 2
        assert events.index("inverse") > events.index("stop", 1)
        with closing(sqlite3.connect(path)) as db:
            assert db.execute(
                "select result from execution_receipts order by id"
            ).fetchall() == [("original",), ("post-start",)]
    assert (checkout / "version").read_text() == "old\n"
    assert (
        json.loads(state.values["repository_upgrade_operation:v1"])["status"]
        == "rolled_back"
    )
