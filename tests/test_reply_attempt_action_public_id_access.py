"""The opt-in reply projections retain source integer primary-key access."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app import action_storage as storage


CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-13-database-redesign-phase5e-reply-source.json").read_text())
ATTEMPT = {"conversation_id": "c", "conversation_title": "t", "trigger_message_id": "m",
           "trigger_sender": "s", "trigger_text": "body", "action": "reply",
           "sensitivity_kind": "ordinary", "send_status": "pending"}


def _steps(db, sql, args=()):
    count = 0

    def tick():
        nonlocal count
        count += 1
        return 0

    db.set_progress_handler(tick, 1)
    try:
        rows = db.execute(sql, args).fetchall()
    finally:
        db.set_progress_handler(None, 0)
    return rows, count


@pytest.mark.parametrize("variant,reply_variant", [("actual", "actual_no_memory_fk"),
                                                    ("fresh_candidate17", "fresh_memory_fk")])
@pytest.mark.parametrize("kind", ["reply_attempts", "memory_write_events"])
def test_public_id_order_and_range_use_bounded_work(variant, reply_variant, kind):
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(":memory:")) as target:
        source.execute("pragma foreign_keys=on")
        target.execute("pragma foreign_keys=on")
        for table in ("reply_attempts", "memory_write_events"):
            source.execute(CAPTURE[variant]["tables"][table]["sql"])
        for definition in CAPTURE[variant]["tables"].values():
            for index in definition["indexes"]:
                if index["sql"]:
                    source.execute(index["sql"])
        target.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
        target.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
        storage.create_action_schema(target, include_reply=True, reply_variant=reply_variant)

        # The fresh Memory variant has an actual source/target Attempt parent.
        source.execute("insert into reply_attempts(id," + ",".join(ATTEMPT) + ") values(50000," +
                       ",".join("?" for _ in ATTEMPT) + ")", tuple(ATTEMPT.values()))
        storage.insert_action(target, "reply_attempts", ATTEMPT | {"id": 50000})
        seed = ATTEMPT if kind == "reply_attempts" else {"attempt_id": 50000, "event_type": "memory_write", "payload_json": "{}"}
        source.execute(f"insert into {kind}(id,{','.join(seed)}) values(-7,{','.join('?' for _ in seed)})", tuple(seed.values()))
        storage.insert_action(target, kind, seed | {"id": -7})

        source_columns = [row[1] for row in source.execute(f"pragma table_info({kind})") if row[1] != "id"]
        target_columns = [row[1] for row in target.execute("pragma table_info(actions)") if row[1] not in ("id", "legacy_key")]

        def add_ids(ids):
            if kind == "memory_write_events":
                for identity in ids:
                    event = {"attempt_id": 50000, "event_type": str(identity), "payload_json": "{}", "id": identity}
                    source.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json) values(?,?,?,?)",
                                   (identity, 50000, str(identity), "{}"))
                    storage.insert_action(target, kind, event)
            else:
                source.executemany(f"insert into {kind}(id,{','.join(source_columns)}) select ?,{','.join(source_columns)} from {kind} where id=-7",
                                   ((identity,) for identity in ids))
                target.executemany("insert into actions(legacy_key," + ",".join(target_columns) + ") select ?," +
                                   ",".join(target_columns) + " from actions where record_type=? and legacy_key='-7'",
                                   ((str(identity), kind) for identity in ids))

        def check_queries():
            projection = storage.reply_action_projection(kind, reply_variant)
            for predicate, args in (("", ()), ("where id between ? and ?", (100, 900))):
                for direction in ("asc", "desc"):
                    suffix = f"{predicate} order by id {direction} limit 5"
                    source_sql = f"select id from {kind} {suffix}"
                    target_sql = f"select id from ({projection}) {suffix}"
                    expected, source_work = _steps(source, source_sql, args)
                    actual, target_work = _steps(target, target_sql, args)
                    assert actual == expected
                    plan = target.execute("explain query plan " + target_sql, args).fetchall()
                    assert not any("USE TEMP B-TREE" in row[3] for row in plan), plan
                    assert any(f"idx_actions_{'reply' if kind == 'reply_attempts' else 'memory'}_public_order" in row[3] for row in plan), plan
                    yield target_work, source_work
            # The Store reader asks for full rows and paginates by public ID.
            source_sql = f"select * from {kind} order by id desc limit ? offset ?"
            target_sql = f"select * from ({projection}) order by id desc limit ? offset ?"
            for offset in (0, 3):
                arguments = (5, offset)
                expected, source_work = _steps(source, source_sql, arguments)
                actual, target_work = _steps(target, target_sql, arguments)
                columns = [item[0] for item in source.execute(source_sql, arguments).description]
                expected_rows = [dict(zip(columns, row, strict=True)) for row in expected]
                actual_rows = [dict(zip(columns, row, strict=True)) for row in actual]
                if kind == "reply_attempts":
                    for row in expected_rows:
                        row["audit_tool_events_json"] = "[]"
                assert actual_rows == expected_rows
                plan = target.execute("explain query plan " + target_sql, arguments).fetchall()
                assert not any("USE TEMP B-TREE" in row[3] for row in plan), plan
                assert any(f"idx_actions_{'reply' if kind == 'reply_attempts' else 'memory'}_public_order" in row[3] for row in plan), plan
                yield target_work, source_work

        add_ids([*range(1, 20), 99999])
        # Both E tables use the same public ID in independent namespaces.
        if kind == "reply_attempts":
            source.execute("insert into memory_write_events(id,attempt_id,event_type,payload_json) values(1,50000,'overlap','{}')")
            storage.insert_action(target, "memory_write_events",
                                  {"id": 1, "attempt_id": 50000, "event_type": "overlap", "payload_json": "{}"})
        else:
            source.execute("insert into reply_attempts(id," + ",".join(ATTEMPT) + ") values(1," +
                           ",".join("?" for _ in ATTEMPT) + ")", tuple(ATTEMPT.values()))
            storage.insert_action(target, "reply_attempts", ATTEMPT | {"id": 1})
        for reply_kind in ("reply_attempts", "memory_write_events"):
            assert source.execute(f"select id from {reply_kind} where id=1").fetchall() == [(1,)]
            assert target.execute("select id from (" + storage.reply_action_projection(reply_kind, reply_variant) +
                                  ") where id=1").fetchall() == [(1,)]
        small = list(check_queries())
        add_ids(range(20, 1020))
        # A different action type shares physical storage and public IDs.
        for identity in range(250):
            storage.insert_action(target, "sent_replies", {"conversation_id": "other", "trigger_message_id": str(identity),
                                                           "reply_text": "body"})
        large = list(check_queries())
        for (small_target, _), (large_target, _) in zip(small, large, strict=True):
            assert large_target <= small_target + 100, (small_target, large_target)
        source.execute(f"delete from {kind} where id=10")
        target.execute("delete from actions where record_type=? and legacy_key='10'", (kind,))
        add_ids([10])
        field = "send_status" if kind == "reply_attempts" else "status"
        expected_reused = source.execute(f"select id,{field} from {kind} where id=10").fetchall()
        actual_reused = target.execute("select id," + field + " from (" +
                                       storage.reply_action_projection(kind, reply_variant) + ") where id=10").fetchall()
        assert expected_reused == actual_reused == [(10, "pending")]
        list(check_queries())
