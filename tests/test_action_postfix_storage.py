"""Postfix action storage against the independently frozen source declarations."""

from contextlib import closing
import hashlib
import inspect
import json
import sqlite3

import pytest

from app import action_storage as storage
from app.run_storage import create_run_schema, insert_run
from app.task_storage import create_task_schema, insert_task
from tests.test_action_receipt_source import source_values as receipt_values
from tests.test_postfix_action_source import SOURCE, CHILDREN


POSTFIX_TYPES = tuple(SOURCE)


def _target(db, *, include_postfix=True):
    db.execute("pragma foreign_keys=on")
    create_task_schema(db)
    create_run_schema(db, include_task_runs=True)
    insert_task(db, "reply_tasks", dict(id=1, conversation_id="chat",
                conversation_title="source", single_chat=0, trigger_message_id="message",
                trigger_create_time="time", trigger_sender="sender"))
    for run_id, role in ((1, "consumer"), (2, "audit")):
        insert_run(db, "agent_runs", dict(id=run_id, task_id=1,
                   execution_generation="generation", role=role))
    storage.create_action_schema(db, include_receipts=True, include_postfix=include_postfix)


@pytest.fixture(params=[None, sqlite3.Row], ids=["tuple", "row"])
def pair(request):
    with closing(sqlite3.connect(":memory:")) as source, closing(sqlite3.connect(":memory:")) as target:
        source.row_factory = target.row_factory = request.param
        source.execute("pragma foreign_keys=on")
        for schema in SOURCE.values():
            for obj in schema["objects"]:
                source.execute(obj["sql"])
        _target(target)
        yield source, target


def _values(kind, key="key", channel="dingtalk", **changes):
    values = dict(channel=channel, delivery_key=key, created_at=" source time ")
    if kind == "outbound_postfixes":
        values.update(final_body=" raw 正文\n", postfix_version=" version ")
    elif kind == "outbound_postfix_receipts":
        values["provider_result_json"] = ' not parsed JSON { "字": 1 } '
    values.update(changes)
    return values


def _insert_source(db, kind, values, *, ignore=False):
    return db.execute(
        f"insert {'or ignore ' if ignore else ''}into {kind}({','.join(values)}) "
        f"values({','.join('?' for _ in values)})", tuple(values.values()),
    )


def _insert_pair(pair, kind, values, *, ignore=False):
    source, target = pair
    original = _insert_source(source, kind, values, ignore=ignore)
    result = storage.insert_action(target, kind, values, ignore=ignore)
    assert result.rowcount == original.rowcount
    assert result.lastrowid == ((values["channel"], values["delivery_key"]) if result.rowcount else None)
    return result


def _snapshots(db, *, target=False):
    return {kind: [tuple(r) for r in db.execute(
        f"select * from ({storage.ACTION_PROJECTIONS[kind]}) order by channel,delivery_key"
        if target else f"select * from {kind} order by channel,delivery_key"
    )] for kind in POSTFIX_TYPES}


@pytest.mark.parametrize("receipts,expected", [
    (False, "21a82e3a36ed0cc4043cb52f91346b9be3aa116d086d8e28b0dcc8b03f98a5ec"),
    (True, "ad91dcd2ae00390707fd3daa7d69e20a831b65ccc5917711249b0e1c85603df5"),
])
def test_pre_postfix_six_and_nine_declarations_remain_exact(receipts, expected):
    # Captured before this extension: every table/index/trigger declaration.
    objects = storage._current_objects(receipts)
    actual = hashlib.sha256(json.dumps(objects, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert actual == expected


def test_postfix_schema_is_an_explicit_twelve_type_opt_in():
    for function in (storage.create_action_schema, storage.action_schema_is_current, storage._current_objects):
        assert "include_postfix" in inspect.signature(function).parameters
    assert tuple(storage.POSTFIX_ACTION_TYPES) == POSTFIX_TYPES
    assert len(storage.ACTION_TYPES) == 6 and len(storage.ALL_ACTION_TYPES) == 9
    assert storage.ALL_ACTION_TYPES_WITH_POSTFIX == storage.ALL_ACTION_TYPES + POSTFIX_TYPES
    assert sum(len(storage.SOURCE_COLUMNS[k]) for k in POSTFIX_TYPES) == 13
    exports = (storage.OUTBOUND_POSTFIX_SQL, storage.OUTBOUND_POSTFIX_RECEIPT_SQL, storage.NATIVE_REPLY_DISPATCH_SQL)
    assert exports == tuple(storage.ACTION_PROJECTIONS[k] for k in POSTFIX_TYPES)
    with closing(sqlite3.connect(":memory:")) as db:
        storage.create_action_schema(db, include_postfix=True)
        assert storage.action_schema_is_current(db, include_postfix=True)
        assert not storage.action_schema_is_current(db)
        assert not storage.action_schema_is_current(db, include_receipts=True)


def test_binary_tuple_keys_raw_values_and_public_physical_ids(pair):
    _, target = pair
    for channel in ("dingtalk", "wechat"):
        for key in ("A", "a", "1", "01", " key ", "\t", "key:part", "中文", 'quote"[x]\\', "zero\0tail"):
            for kind in POSTFIX_TYPES:
                _insert_pair(pair, kind, _values(kind, key, channel, created_at=" exact source time "))
            physical = storage.action_physical_id(target, "outbound_postfixes", (channel, key))
            raw_key = target.execute("select legacy_key from actions where id=?", (physical,)).fetchone()[0]
            assert json.loads(raw_key) == [channel, key]
    assert _snapshots(pair[0]) == _snapshots(target, target=True)
    assert not target.execute("pragma foreign_key_check").fetchall()


def test_source_defaults_are_applied_only_when_omitted(pair):
    before = pair[0].execute("select current_timestamp").fetchone()[0]
    for kind in POSTFIX_TYPES:
        values = _values(kind)
        del values["created_at"]
        _insert_pair(pair, kind, values)
    after = pair[1].execute("select current_timestamp").fetchone()[0]
    for db, target in ((pair[0], False), (pair[1], True)):
        rows = _snapshots(db, target=target)
        assert rows["outbound_postfixes"][0][3] == ""
        assert all(before <= rows[kind][0][-1] <= after for kind in POSTFIX_TYPES)


def test_equivalent_json_encodings_share_the_same_composite_identity(pair):
    target = pair[1]
    kind = "outbound_postfixes"
    _insert_pair(pair, kind, _values(kind, key="中文"))
    physical = storage.action_physical_id(target, kind, ("dingtalk", "中文"))
    alternate = json.dumps(["dingtalk", "中文"], ensure_ascii=True, indent=2)
    target.execute("update actions set legacy_key=? where id=?", (alternate, physical))
    assert storage.action_physical_id(target, kind, ("dingtalk", "中文")) == physical
    with pytest.raises(sqlite3.IntegrityError):
        storage.insert_action(target, kind, _values(kind, key="中文"))
    assert storage.update_action(target, kind, ("dingtalk", "中文"), {"delivery_key": " next "},
                                 where_sql="channel=?", parameters=("dingtalk",)).rowcount == 1
    assert storage.action_physical_id(target, kind, ("dingtalk", " next ")) == physical
    assert storage.delete_action(target, kind, ("dingtalk", " next ")).rowcount == 1


@pytest.mark.parametrize("field,value", [
    ("channel", "DINGTALK"), ("channel", ""), ("channel", None),
    ("delivery_key", ""), ("delivery_key", "  "), ("delivery_key", None),
    ("final_body", ""), ("final_body", "  "), ("final_body", None),
    ("feedback_token", None), ("postfix_version", None), ("created_at", None),
])
def test_parent_constraints_match_source(pair, field, value):
    values = _values("outbound_postfixes", **{field: value})
    with pytest.raises(sqlite3.IntegrityError):
        _insert_source(pair[0], "outbound_postfixes", values)
    with pytest.raises(sqlite3.IntegrityError):
        storage.insert_action(pair[1], "outbound_postfixes", values)
    assert not pair[1].execute("select 1 from actions").fetchone()


@pytest.mark.parametrize("kind", CHILDREN)
def test_child_composite_fk_and_ignore_match_source(pair, kind):
    _insert_pair(pair, "outbound_postfixes", _values("outbound_postfixes"))
    for values in (_values(kind, channel="wechat"), _values(kind, key="missing")):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_source(pair[0], kind, values, ignore=True)
        with pytest.raises(sqlite3.IntegrityError):
            storage.insert_action(pair[1], kind, values, ignore=True)
    _insert_pair(pair, kind, _values(kind))
    _insert_pair(pair, kind, _values(kind), ignore=True)
    assert _snapshots(pair[0]) == _snapshots(pair[1], target=True)


@pytest.mark.parametrize("kind", CHILDREN)
def test_parent_key_restrictions_child_rebinding_and_partial_key_update(pair, kind):
    source, target = pair
    for channel, key in (("dingtalk", "key"), ("dingtalk", "other"), ("wechat", "other")):
        _insert_pair(pair, "outbound_postfixes", _values("outbound_postfixes", key, channel, created_at="time"))
    _insert_pair(pair, kind, _values(kind, created_at="time"))
    for values in ({"delivery_key": "changed"}, {"channel": "wechat"}):
        with pytest.raises(sqlite3.IntegrityError):
            source.execute(f"update outbound_postfixes set {next(iter(values))}=? where delivery_key='key'", tuple(values.values()))
        with pytest.raises(sqlite3.IntegrityError):
            storage.update_action(target, "outbound_postfixes", ("dingtalk", "key"), values)
    with pytest.raises(sqlite3.IntegrityError):
        source.execute("delete from outbound_postfixes where delivery_key='key'")
    with pytest.raises(sqlite3.IntegrityError):
        storage.delete_action(target, "outbound_postfixes", ("dingtalk", "key"))
    source.execute(f"update {kind} set delivery_key='other' where delivery_key='key'")
    assert storage.update_action(target, kind, ("dingtalk", "key"), {"delivery_key": "other"}).rowcount == 1
    source.execute(f"update {kind} set channel='wechat' where delivery_key='other'")
    assert storage.update_action(target, kind, ("dingtalk", "other"), {"channel": "wechat"}).rowcount == 1
    source.execute("update outbound_postfixes set delivery_key='changed' where delivery_key='key'")
    storage.update_action(target, "outbound_postfixes", ("dingtalk", "key"), {"delivery_key": "changed"})
    assert _snapshots(source) == _snapshots(target, target=True)
    assert storage.delete_action(target, kind, ("wechat", "other")).rowcount == 1
    assert storage.delete_action(target, kind, ("wechat", "other")).rowcount == 0


def test_raw_payload_updates_and_projection_cas_preserve_source_semantics(pair):
    source, target = pair
    for kind in POSTFIX_TYPES:
        _insert_pair(pair, kind, _values(kind, created_at=" raw time "))
    updates = {"final_body": " revised\0正文 ", "feedback_token": " raw token ", "postfix_version": "v2"}
    source.execute("update outbound_postfixes set final_body=?,feedback_token=?,postfix_version=?", tuple(updates.values()))
    assert storage.update_action(target, "outbound_postfixes", ("dingtalk", "key"), updates,
                                 where_sql="postfix_version=? and delivery_key=?", parameters=(" version ", "key")).rowcount == 1
    assert storage.update_action(target, "outbound_postfixes", ("dingtalk", "key"), {"final_body": "not applied"},
                                 where_sql="postfix_version=?", parameters=(" version ",)).rowcount == 0
    source.execute("update outbound_postfix_receipts set provider_result_json=' still not JSON '")
    storage.update_action(target, "outbound_postfix_receipts", ("dingtalk", "key"), {"provider_result_json": " still not JSON "})
    assert _snapshots(source) == _snapshots(target, target=True)


def test_native_child_does_not_gain_parent_checks_when_foreign_keys_are_off(pair):
    for db in pair:
        db.commit()
        db.execute("pragma foreign_keys=off")
    _insert_pair(pair, "native_reply_dispatches", _values("native_reply_dispatches", key="  ", channel="OTHER", created_at="time"))
    assert _snapshots(pair[0]) == _snapshots(pair[1], target=True)


@pytest.mark.parametrize("bad", ["dingtalk:key", ["dingtalk", "key"], ("key",), ("dingtalk", 1)])
def test_public_tuple_identity_is_not_guessed_or_coerced(pair, bad):
    target = pair[1]
    _insert_pair(pair, "outbound_postfixes", _values("outbound_postfixes"))
    before = tuple(target.iterdump())
    for operation in (lambda: storage.action_physical_id(target, "outbound_postfixes", bad),
                      lambda: storage.delete_action(target, "outbound_postfixes", bad),
                      lambda: storage.update_action(target, "outbound_postfixes", bad, {"final_body": "changed"})):
        with pytest.raises(ValueError):
            operation()
    assert tuple(target.iterdump()) == before


def test_twelve_type_payload_is_finite_and_composite_fk_is_physical(pair):
    target = pair[1]
    _insert_pair(pair, "outbound_postfixes", _values("outbound_postfixes"))
    with pytest.raises(sqlite3.IntegrityError):
        target.execute("update actions set payload_json=json_set(payload_json,'$.invented',1)")
    for raw in ('[]', '["dingtalk"]', '["dingtalk","key",3]', '["dingtalk",1]', '{"channel":"dingtalk"}'):
        with pytest.raises(sqlite3.IntegrityError):
            target.execute("update actions set legacy_key=?", (raw,))
    fk = [tuple(r) for r in target.execute("pragma foreign_key_list(actions)") if r[3] in ("postfix_type", "channel", "delivery_key")]
    assert len(fk) == 3 and len({r[0] for r in fk}) == 1
    assert [(r[2], r[3], r[4], r[5], r[6]) for r in sorted(fk, key=lambda r: r[1])] == [
        ("actions", "postfix_type", "record_type", "NO ACTION", "NO ACTION"),
        ("actions", "channel", "channel", "NO ACTION", "NO ACTION"),
        ("actions", "delivery_key", "delivery_key", "NO ACTION", "NO ACTION"),
    ]


@pytest.mark.parametrize("field,value", [("delivery_key", b"raw"), ("final_body", b"\x00\xff"), ("feedback_token", 1)])
def test_non_text_source_storage_is_explicitly_refused_before_mutation(pair, field, value):
    target = pair[1]
    before = tuple(target.iterdump())
    with pytest.raises(ValueError, match="unrepresentable"):
        storage.insert_action(target, "outbound_postfixes", _values("outbound_postfixes", **{field: value}))
    assert tuple(target.iterdump()) == before


def _populate_nine(db):
    records = {
        "review_candidates": dict(id=1, task_id=1, execution_generation="generation", consumer_run_id=1,
                                  stage_index=0, proposal_revision=1, candidate_digest="raw digest", candidate_json=" raw candidate "),
        "candidate_reviews": dict(id=1, candidate_id=1, audit_run_id=2, decision="approve", candidate_digest="raw digest", result_json=" raw review "),
        "candidate_selections": dict(id=1, candidate_id=1, review_id=1, option_key="option", branch_json="branch"),
        "candidate_supplements": dict(id=1, candidate_id=1, instruction="instruction"),
        "candidate_executions": dict(id=1, candidate_id=1, review_id=1, selection_id=1, status="uncertain"),
        "candidate_action_attempts": dict(id=1, execution_id=1, action_index=0, external_action_key="1", status="verified"),
    }
    records.update({k: receipt_values(k, token='external:{"opaque":' if k == "external_action_results" else "1")
                    for k in storage.RECEIPT_ACTION_TYPES})
    for kind, values in records.items():
        values.update({field: " original time " for field in ("created_at", "updated_at", "prepared_at") if field in storage.SOURCE_COLUMNS[kind]})
        storage.insert_action(db, kind, values)
    for kind in storage.AUTOINCREMENT_TYPES:
        storage.insert_action(db, kind, {**receipt_values(kind, token="deleted-high"), "id": 700})
        storage.delete_action(db, kind, 700)


def test_postfix_writes_preserve_all_nine_type_rows_and_allocator_semantics(pair):
    target = pair[1]
    _populate_nine(target)
    rows = {kind: [tuple(r) for r in target.execute(storage.ACTION_PROJECTIONS[kind])] for kind in storage.ALL_ACTION_TYPES}
    physical = [tuple(r) for r in target.execute("select id,record_type,legacy_key,payload_json,task_id,candidate_id,review_id,selection_id,execution_id from actions order by id")]
    sequence = [tuple(r) for r in target.execute("select * from sqlite_sequence order by name")]
    for kind in POSTFIX_TYPES:
        _insert_pair(pair, kind, _values(kind))
    assert {kind: [tuple(r) for r in target.execute(storage.ACTION_PROJECTIONS[kind])] for kind in storage.ALL_ACTION_TYPES} == rows
    assert [tuple(r) for r in target.execute("select id,record_type,legacy_key,payload_json,task_id,candidate_id,review_id,selection_id,execution_id from actions where record_type not in (?,?,?) order by id", POSTFIX_TYPES)] == physical
    assert [tuple(r) for r in target.execute("select * from sqlite_sequence order by name")] == sequence
    for kind in storage.AUTOINCREMENT_TYPES:
        assert storage.insert_action(target, kind, receipt_values(kind, token="next")).lastrowid == 701
        assert storage.insert_action(target, kind, receipt_values(kind, token="next"), ignore=True).rowcount == 0
        assert storage.insert_action(target, kind, receipt_values(kind, token="later")).lastrowid == 703
    assert not target.execute("pragma foreign_key_check").fetchall()


def test_opt_in_repair_only_recreates_missing_matching_objects(pair):
    target = pair[1]
    index = target.execute("select name from sqlite_master where type='index' and sql like '%record_type,channel,delivery_key%'").fetchone()[0]
    target.execute(f'drop index "{index}"')
    assert not storage.action_schema_is_current(target, include_postfix=True)
    assert storage.action_schema_is_current(target, include_postfix=True, allow_missing_objects=True)
    storage.create_action_schema(target, include_postfix=True)
    assert storage.action_schema_is_current(target, include_postfix=True)
    target.execute("create table native_reply_dispatches(unexpected text)")
    before = tuple(target.iterdump())
    with pytest.raises(ValueError):
        storage.create_action_schema(target, include_postfix=True)
    assert tuple(target.iterdump()) == before
