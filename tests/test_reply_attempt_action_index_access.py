"""Source/typed access parity for every original Attempt and Memory lookup index."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from app import action_storage as storage


CAPTURE = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/"
                      "2026-10-13-database-redesign-phase5e-reply-source.json").read_text())
VARIANTS = (("actual", "actual_no_memory_fk"), ("fresh_candidate17", "fresh_memory_fk"))


def steps(db, sql, args):
    work = 0

    def tick():
        nonlocal work
        work += 1
        return 0

    db.set_progress_handler(tick, 1)
    try:
        result = db.execute(sql, args).fetchall()
    finally:
        db.set_progress_handler(None, 0)
    return result, work


def setup(variant, target_variant):
    source = sqlite3.connect(":memory:")
    target = sqlite3.connect(":memory:")
    source.execute("pragma foreign_keys=on")
    target.execute("pragma foreign_keys=on")
    for table in ("reply_attempts", "memory_write_events"):
        definition = CAPTURE[variant]["tables"][table]
        source.execute(definition["sql"])
        for index in definition["indexes"]:
            if index["sql"]:
                source.execute(index["sql"])
    target.execute("create table runs(id integer primary key,record_type text,legacy_key text)")
    target.execute("create table tasks(id integer primary key,record_type text,legacy_key text)")
    storage.create_action_schema(target, include_reply=True, reply_variant=target_variant)
    return source, target


def seed(source, target):
    for i in range(20):
        attempt = {
            "id": 101 + i,
            "conversation_id": "focused" if i < 12 else "other",
            "conversation_title": "title",
            "trigger_message_id": "needle" if i < 8 else "other",
            "trigger_sender": "sender",
            "trigger_text": "raw\x00value" if i == 0 else f"body-{i}",
            "action": "reply" if i < 10 else "skip",
            "sensitivity_kind": "ordinary",
            "send_status": "pending" if i < 10 else "done",
            "send_error": "retry\x00raw" if i < 7 else "",
            "agent_run_id": 7 if i < 8 else 9,
            "oa_process_instance_id": "oa-1" if i < 6 else "",
            "channel": "dingtalk" if i < 12 else "email",
            "created_at": "2026-01-01 00:00:00" if i < 6 else f"2026-01-01 00:00:{i:02}",
            "updated_at": f"2026-01-02 00:00:{i:02}",
        }
        source.execute(
            "insert into reply_attempts(" + ",".join(attempt) + ") values(" +
            ",".join("?" for _ in attempt) + ")", tuple(attempt.values()))
        storage.insert_action(target, "reply_attempts", attempt)
        event = {
            "id": 201 + i,
            "attempt_id": attempt["id"],
            "event_type": "memory_write",
            "payload_json": "{}",
            "status": "pending" if i < 10 else "written",
            "attempts": 1,
            "last_error": "raw\x00error" if i == 0 else "",
            "memory_episode_id": f"episode-{i}",
            "created_at": f"2026-01-03 00:00:{i:02}",
            "updated_at": f"2026-01-04 00:00:{i:02}",
        }
        source.execute(
            "insert into memory_write_events(" + ",".join(event) + ") values(" +
            ",".join("?" for _ in event) + ")", tuple(event.values()))
        storage.insert_action(target, "memory_write_events", event)


def cases(variant):
    attempt_fields = "id,trigger_text,send_status,agent_run_id,send_error,oa_process_instance_id"
    memory_fields = "id,attempt_id,status,attempts,last_error,memory_episode_id"
    return (
        ("reply_attempts", "trigger_message_id=?", ("needle",),
         "idx_reply_attempts_trigger_message_id", "idx_actions_reply_trigger_message", attempt_fields),
        ("reply_attempts", "send_status=? order by created_at limit 5", ("pending",),
         "idx_reply_attempts_status", "idx_actions_reply_status", attempt_fields),
        ("reply_attempts", "created_at=? order by id limit 5", ("2026-01-01 00:00:00",),
         "idx_reply_attempts_created", "idx_actions_reply_created", attempt_fields),
        ("reply_attempts", "oa_process_instance_id<>'' and oa_process_instance_id=? "
         "order by created_at desc,id desc limit 5", ("oa-1",),
         "idx_reply_attempts_oa_history", "idx_actions_reply_oa_history", attempt_fields),
        ("reply_attempts", "conversation_id=? and trigger_message_id=? and action=? "
         "order by id desc limit 5", ("focused", "needle", "reply"),
         "idx_reply_attempts_trigger_history", "idx_actions_reply_trigger_history", attempt_fields),
        ("reply_attempts", "channel=? and conversation_id=? and trigger_message_id=? "
         "order by updated_at desc,id desc limit 5", ("dingtalk", "focused", "needle"),
         "idx_reply_attempts_current_trigger", "idx_actions_reply_current_trigger", attempt_fields),
        ("reply_attempts", "agent_run_id=? and send_error=? order by id limit 5",
         (7, "retry\x00raw"), "idx_reply_attempts_agent_run_recovery",
         "idx_actions_reply_run_recovery", attempt_fields),
        ("memory_write_events",
         "attempt_id=? order by id limit 5" if variant == "actual" else
         "attempt_id between ? and ?",
         (101,) if variant == "actual" else (101, 108),
         "idx_memory_write_events_attempt", "idx_actions_memory_attempt",
         memory_fields),
        ("memory_write_events",
         "status=? order by " + ("id" if variant == "actual" else "updated_at") + " limit 5",
         ("pending",), "idx_memory_write_events_status", "idx_actions_memory_status",
         memory_fields),
    )


def check(source, target, target_variant, index_cases):
    works = []
    for table, suffix, args, source_index, target_index, fields in index_cases:
        projection = storage.reply_action_projection(table, target_variant)
        old_sql = f"select {fields} from {table} where {suffix}"
        new_sql = f"select {fields} from ({projection}) where {suffix}"
        old_rows, old_work = steps(source, old_sql, args)
        new_rows, new_work = steps(target, new_sql, args)
        assert old_rows, (table, suffix)
        if "order by" in suffix:
            assert new_rows == old_rows, (table, suffix)
        else:
            assert sorted(new_rows) == sorted(old_rows), (table, suffix)
        old_plan = [row[3] for row in source.execute("explain query plan " + old_sql, args)]
        new_plan = [row[3] for row in target.execute("explain query plan " + new_sql, args)]
        assert any(source_index in detail for detail in old_plan), (source_index, old_plan)
        assert any(target_index in detail for detail in new_plan), (target_index, new_plan)
        assert not any("USE TEMP B-TREE" in detail for detail in new_plan), new_plan
        works.append((new_work, old_work))
    return works


@pytest.mark.parametrize("variant,target_variant", VARIANTS)
def test_all_original_reply_lookup_paths_bound_unrelated_action_growth(variant, target_variant):
    source, target = setup(variant, target_variant)
    with closing(source), closing(target):
        for i in range(20):
            storage.insert_action(target, "sent_replies", {
                "conversation_id":"unrelated", "trigger_message_id":str(i),
                "reply_text":"other"})
        seed(source, target)
        index_cases = cases(variant)
        small = check(source, target, target_variant, index_cases)
        for i in range(20, 1020):
            storage.insert_action(target, "sent_replies", {
                "conversation_id":"unrelated", "trigger_message_id":str(i),
                "reply_text":"other"})
        large = check(source, target, target_variant, index_cases)
        for (small_target, small_source), (large_target, large_source) in zip(
                small, large, strict=True):
            assert large_source <= small_source + 10
            assert large_target <= small_target + 100, (small_target, large_target)


def original_shape(source, target, target_variant, table, suffix, arguments, source_index):
    projection = storage.reply_action_projection(table, target_variant)
    source_sql = f"select id from {table} where {suffix}"
    target_sql = f"select id from ({projection}) where {suffix}"
    source_rows, source_work = steps(source, source_sql, arguments)
    target_rows, target_work = steps(target, target_sql, arguments)
    assert source_rows and target_rows == source_rows
    source_plan = [row[3] for row in source.execute(
        "explain query plan " + source_sql, arguments)]
    target_plan = [row[3] for row in target.execute(
        "explain query plan " + target_sql, arguments)]
    assert any(source_index in detail for detail in source_plan), source_plan
    return source_work, target_work, target_plan


@pytest.mark.parametrize("variant,target_variant", VARIANTS)
def test_original_created_range_access_stays_bounded_as_attempts_grow(
        variant, target_variant):
    source, target = setup(variant, target_variant)
    with closing(source), closing(target):
        seed(source, target)
        suffix = "created_at>? order by created_at,id limit 5"
        args = ("2026-01-01 00:00:09",)
        before = original_shape(source, target, target_variant, "reply_attempts",
                                suffix, args, "idx_reply_attempts_created")
        for i in range(1000):
            attempt = {
                "id": 1000 + i, "conversation_id": "volume",
                "conversation_title": "volume", "trigger_message_id": f"volume-{i}",
                "trigger_sender": "volume", "trigger_text": "volume",
                "action": "reply", "sensitivity_kind": "ordinary",
                "send_status": "done", "created_at": "2025-01-01 00:00:00",
            }
            source.execute(
                "insert into reply_attempts(" + ",".join(attempt) + ") values(" +
                ",".join("?" for _ in attempt) + ")", tuple(attempt.values()))
            storage.insert_action(target, "reply_attempts", attempt)
        after = original_shape(source, target, target_variant, "reply_attempts",
                               suffix, args, "idx_reply_attempts_created")
        assert after[0] <= before[0] + 10
        assert after[1] <= before[1] + 100, (before, after)
        assert any("idx_actions_reply_created" in detail for detail in after[2]), after[2]
        assert not any("USE TEMP B-TREE" in detail for detail in after[2]), after[2]


@pytest.mark.parametrize("variant,target_variant", VARIANTS)
def test_original_memory_parent_access_stays_bounded_as_events_grow(
        variant, target_variant):
    source, target = setup(variant, target_variant)
    with closing(source), closing(target):
        seed(source, target)
        suffix = "attempt_id=? order by id limit 5"
        args = (101,)
        before = original_shape(source, target, target_variant, "memory_write_events",
                                suffix, args, "idx_memory_write_events_attempt")
        for i in range(1000):
            event = {
                "id": 2000 + i, "attempt_id": 102,
                "event_type": f"volume-{i}", "payload_json": "{}", "status": "written",
            }
            source.execute(
                "insert into memory_write_events(" + ",".join(event) + ") values(" +
                ",".join("?" for _ in event) + ")", tuple(event.values()))
            storage.insert_action(target, "memory_write_events", event)
        after = original_shape(source, target, target_variant, "memory_write_events",
                               suffix, args, "idx_memory_write_events_attempt")
        assert after[0] <= before[0] + 10
        assert after[1] <= before[1] + 100, (before, after)
        assert any("idx_actions_memory_attempt" in detail for detail in after[2]), after[2]
        assert not any("USE TEMP B-TREE" in detail for detail in after[2]), after[2]
