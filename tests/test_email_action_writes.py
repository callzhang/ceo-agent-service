"""Email action mutations compared with the frozen native source."""

import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

import pytest

from app.action_storage import create_action_schema, email_action_projection
from tests.test_email_action_storage import _cases, _source_tables


def _databases(*, include_skill_config_receipts=False, compact_payload=True):
    source = sqlite3.connect(":memory:")
    target = sqlite3.connect(":memory:")
    for db in (source, target):
        db.execute("pragma foreign_keys=on")
        db.execute("create table email_classifications(id integer primary key)")
        db.execute("insert into email_classifications values(11)")
    target.execute("create table tasks(id integer primary key, record_type text)")
    target.execute("create table runs(id integer primary key, record_type text)")
    for table in _source_tables("actual").values():
        source.execute(table["sql"])
        for index in reversed(table["indexes"]):
            if index["sql"]:
                source.execute(index["sql"])
        for trigger in table["triggers"]:
            source.execute(trigger["sql"])
    if include_skill_config_receipts:
        from tests.configs_source_fixtures import ROOT
        import json
        business = json.loads((ROOT / "docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json").read_text())["actual"]["tables"]
        for name in ("business_tasks", "business_task_signals"):
            target.execute(business[name]["sql"])
        create_action_schema(target, include_reply=True, reply_variant="fresh_memory_fk",
                             include_email=True, include_followup_sync=True,
                             include_skill_config_receipts=True, compact_payload=compact_payload)
    else:
        create_action_schema(target, include_reply=True, reply_variant="fresh_memory_fk", include_email=True,
                             compact_payload=compact_payload)
    return source, target


def test_all_thirteen_native_inserts_project_same_values_and_types():
    from app.action_storage import insert_action, insert_email_action

    source, target = _databases()
    with closing(source), closing(target):
        for kind, values in _cases().items():
            fields = tuple(c["name"] for c in _source_tables("actual")[kind]["columns"])
            native = source.execute(
                f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            result = (insert_email_action(target, kind, values) if kind in (
                "email_action_plans", "email_actions", "email_unsubscribe_cancellations",
                "email_unsubscribe_claims", "email_unsubscribe_continuations",
                "email_unsubscribe_receipts", "email_feedback_requests",
                "email_reply_dispatch_claims", "email_reply_receipts",
            ) else insert_action(target, kind, values))
            assert result.rowcount == 1
            if kind in ("email_action_plans", "email_actions", "email_unsubscribe_cancellations",
                        "email_unsubscribe_claims", "email_unsubscribe_continuations",
                        "email_unsubscribe_receipts", "email_feedback_requests",
                        "email_reply_dispatch_claims", "email_reply_receipts",
                        "email_action_attempts", "email_unsubscribe_steps"):
                assert result.lastrowid == native.lastrowid
            projection = email_action_projection(kind)
            assert target.execute(projection).fetchall() == source.execute(
                f"select {','.join(fields)} from {kind}"
            ).fetchall(), kind
            assert target.execute("select " + ",".join(f"typeof({f})" for f in fields) +
                                  " from (" + projection + ")").fetchall() == source.execute(
                "select " + ",".join(f"typeof({f})" for f in fields) + f" from {kind}"
            ).fetchall(), kind


def test_nullable_public_key_and_signed_rowid_allocation_match_native():
    from app.action_storage import insert_email_action, update_email_action, delete_email_action

    source, target = _databases()
    kind = "email_unsubscribe_cancellations"
    with closing(source), closing(target):
        for explicit in (-4, 0, None, None):
            values = {"decision_json": "{}"}
            if explicit is None:
                native = source.execute(f"insert into {kind}(decision_json) values(?)", ("{}",))
                result = insert_email_action(target, kind, values)
            else:
                native = source.execute(f"insert into {kind}(rowid,decision_json) values(?,?)", (explicit, "{}"))
                result = insert_email_action(target, kind, values, source_rowid=explicit)
            assert result.lastrowid == native.lastrowid
        assert [row[0] for row in source.execute(f"select rowid from {kind} order by rowid")] == [-4, 0, 1, 2]
        assert target.execute(email_action_projection(kind, include_rowid=True) + " order by rowid").fetchall() == source.execute(
            f"select rowid,action_identity,decision_json from {kind} order by rowid"
        ).fetchall()
        source.execute(f"delete from {kind} where rowid=2")
        delete_email_action(target, kind, 2)
        assert insert_email_action(target, kind, {"decision_json": "{}"}).lastrowid == source.execute(
            f"insert into {kind}(decision_json) values('{{}}')"
        ).lastrowid == 2
        source.execute(f"update {kind} set action_identity='public' where rowid=1")
        update_email_action(target, kind, 1, {"action_identity": "public"})
        assert target.execute("select * from (" + email_action_projection(kind, include_rowid=True) + ") where rowid=1").fetchone() == (
            1, "public", "{}"
        )
        source.execute(f"update {kind} set rowid=7 where rowid=1")
        update_email_action(target, kind, 1, {}, new_source_rowid=7)
        assert target.execute("select * from (" + email_action_projection(kind, include_rowid=True) + ") where rowid=7").fetchone() == (
            7, "public", "{}"
        )


def test_email_auto_ids_keep_independent_watermarks_after_deleted_high():
    from app.action_storage import insert_action, delete_action

    source, target = _databases()
    with closing(source), closing(target):
        cases = _cases()
        for kind in ("email_action_plans", "email_actions"):
            values = cases[kind]
            source.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                           tuple(values.values()))
            from app.action_storage import insert_email_action
            insert_email_action(target, kind, values)
        for kind, values in (("email_action_attempts", cases["email_action_attempts"]),
                             ("email_unsubscribe_steps", cases["email_unsubscribe_steps"])):
            if kind == "email_unsubscribe_steps":
                claim = cases["email_unsubscribe_claims"]
                source.execute(f"insert into email_unsubscribe_claims({','.join(claim)}) values({','.join('?' for _ in claim)})",
                               tuple(claim.values()))
                insert_email_action(target, "email_unsubscribe_claims", claim)
            native = source.execute(f"insert into {kind}(id,{','.join(values)}) values(50,{','.join('?' for _ in values)})",
                                    tuple(values.values()))
            result = insert_action(target, kind, {"id": 50} | values)
            assert native.lastrowid == result.lastrowid == 50
            source.execute(f"delete from {kind} where id=50")
            delete_action(target, kind, 50)
            assert source.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                                  tuple(values.values())).lastrowid == insert_action(target, kind, values).lastrowid == 51


def test_ignored_explicit_auto_id_advances_native_watermark_but_update_does_not():
    from app.action_storage import insert_action, update_action

    source, target = _databases()
    with closing(source), closing(target):
        cases = _cases()
        for kind in ("email_action_plans", "email_actions"):
            values = cases[kind]
            source.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                           tuple(values.values()))
            from app.action_storage import insert_email_action
            insert_email_action(target, kind, values)
        kind = "email_action_attempts"
        values = cases[kind]
        columns = ",".join(values)
        marks = ",".join("?" for _ in values)
        source.execute(f"insert into {kind}(id,{columns}) values(1,{marks})", tuple(values.values()))
        insert_action(target, kind, {"id": 1} | values)
        invalid = {"id": 900, **values, "attempt_number": 1}
        source.execute(f"insert or ignore into {kind}(id,{columns}) values(?,{marks})", (900, *values.values()))
        assert insert_action(target, kind, invalid, ignore=True).rowcount == 0
        native_seq = source.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0]
        target_seq = target.execute("select seq from sqlite_sequence where name=?", ("actions:" + kind,)).fetchone()[0]
        assert target_seq == native_seq
        bad_check = values | {"id": 950, "status": "invalid"}
        bad_columns = ",".join(bad_check)
        source.execute(f"insert or ignore into {kind}({bad_columns}) values({','.join('?' for _ in bad_check)})",
                       tuple(bad_check.values()))
        assert insert_action(target, kind, bad_check, ignore=True).rowcount == 0
        native_seq = source.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0]
        target_seq = target.execute("select seq from sqlite_sequence where name=?", ("actions:" + kind,)).fetchone()[0]
        assert target_seq == native_seq
        automatic_bad = values | {"status": "invalid"}
        native_columns = ",".join(automatic_bad)
        source.execute(f"insert or ignore into {kind}({native_columns}) values({','.join('?' for _ in automatic_bad)})",
                       tuple(automatic_bad.values()))
        assert insert_action(target, kind, automatic_bad, ignore=True).rowcount == 0
        native_seq = source.execute("select seq from sqlite_sequence where name=?", (kind,)).fetchone()[0]
        target_seq = target.execute("select seq from sqlite_sequence where name=?", ("actions:" + kind,)).fetchone()[0]
        assert target_seq == native_seq
        source.execute(f"update {kind} set id=1000 where id=1")
        update_action(target, kind, 1, {"id": 1000})
        assert target.execute("select seq from sqlite_sequence where name=?", ("actions:" + kind,)).fetchone()[0] == native_seq
        newer = values | {"attempt_number": 2}
        assert source.execute(f"insert into {kind}({columns}) values({marks})", tuple(newer.values())).lastrowid == insert_action(
            target, kind, newer
        ).lastrowid


def test_composite_source_affinity_partial_update_and_lookup():
    from app.action_storage import insert_action, update_action, delete_action

    source, target = _databases()
    with closing(source), closing(target):
        source.commit()
        target.commit()
        source.execute("pragma foreign_keys=off")
        target.execute("pragma foreign_keys=off")
        kind = "email_direct_retry_requests"
        values = _cases()[kind] | {"action_id": b"binary", "attempt_number": "2.0"}
        columns = ",".join(values)
        source.execute(f"insert into {kind}({columns}) values({','.join('?' for _ in values)})", tuple(values.values()))
        result = insert_action(target, kind, values)
        native = source.execute(f"select action_id,attempt_number from {kind}").fetchone()
        assert target.execute(email_action_projection(kind)).fetchone()[:2] == native
        assert result.lastrowid == native
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            source.execute(f"update {kind} set attempt_number=3 where action_id=?", (b"binary",))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            update_action(target, kind, (b"binary", 2), {"attempt_number": 3})
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            delete_action(target, kind, (b"binary", 2))


def test_mutable_composite_partial_key_update_uses_projected_identity():
    from app.action_storage import insert_action, update_action, delete_action

    source, target = _databases()
    with closing(source), closing(target):
        source.commit()
        target.commit()
        source.execute("pragma foreign_keys=off")
        target.execute("pragma foreign_keys=off")
        kind = "email_unsubscribe_effects"
        values = _cases()[kind] | {"action_identity": 3.5, "effect_digest": b"old"}
        columns = ",".join(values)
        source.execute(f"insert into {kind}({columns}) values({','.join('?' for _ in values)})", tuple(values.values()))
        identity = insert_action(target, kind, values).lastrowid
        assert identity == source.execute(f"select action_identity,effect_digest from {kind}").fetchone()
        source.execute(f"update {kind} set effect_digest=?,previous_effect_digest=? where action_identity=? and effect_digest=?",
                       (b"new", "p" * 64, *identity))
        cursor = update_action(target, kind, identity, {"effect_digest": b"new", "previous_effect_digest": "p" * 64},
                               where_sql="created_at=?", parameters=("time",))
        assert cursor.rowcount == 1
        assert target.execute(email_action_projection(kind)).fetchone() == source.execute(
            f"select {','.join(c['name'] for c in _source_tables('actual')[kind]['columns'])} from {kind}"
        ).fetchone()
        assert delete_action(target, kind, ("3.5", b"new")).rowcount == 1


def test_rowid_null_update_and_unsupported_binding_follow_native_order():
    from app.action_storage import insert_email_action, update_email_action

    source, target = _databases()
    kind = "email_unsubscribe_cancellations"
    with closing(source), closing(target):
        source.execute(f"insert into {kind}(decision_json) values('{{}}')")
        insert_email_action(target, kind, {"decision_json": "{}"})
        with pytest.raises(sqlite3.IntegrityError, match="datatype mismatch"):
            source.execute(f"update {kind} set rowid=null where rowid=1")
        with pytest.raises(sqlite3.IntegrityError, match="datatype mismatch"):
            update_email_action(target, kind, 1, {}, new_source_rowid=None)
        with pytest.raises(sqlite3.ProgrammingError):
            update_email_action(target, kind, 999, {"decision_json": object()})
        with pytest.raises(sqlite3.ProgrammingError):
            source.execute(f"update {kind} set decision_json=? where rowid=999", (object(),))


@pytest.mark.parametrize("isolation_level", ("DEFERRED", None))
def test_two_connections_allocate_distinct_rowids_and_preserve_updates(isolation_level):
    from app.action_storage import insert_email_action, update_email_action

    with TemporaryDirectory(prefix="email-writes-", dir="/tmp") as directory:
        path = Path(directory) / "actions.sqlite"
        with closing(sqlite3.connect(path)) as setup:
            setup.execute("create table tasks(id integer primary key, record_type text)")
            setup.execute("create table runs(id integer primary key, record_type text)")
            setup.execute("create table email_classifications(id integer primary key)")
            create_action_schema(setup, include_reply=True, reply_variant="fresh_memory_fk", include_email=True, compact_payload=True)
            setup.commit()
        barrier = Barrier(2)

        def insert_one(index):
            with closing(sqlite3.connect(path, timeout=5, isolation_level=isolation_level)) as db:
                if isolation_level:
                    db.execute("begin")
                barrier.wait()
                result = insert_email_action(db, "email_unsubscribe_cancellations", {"decision_json": "{}"})
                db.commit()
                return result.lastrowid

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(insert_one, (0, 1)))
        assert set(ids) == {1, 2}
        barrier = Barrier(2)

        def update_one(field, value):
            with closing(sqlite3.connect(path, timeout=5, isolation_level=isolation_level)) as db:
                if isolation_level:
                    db.execute("begin")
                barrier.wait()
                result = update_email_action(db, "email_unsubscribe_cancellations", 1, {field: value})
                db.commit()
                return result.rowcount

        with ThreadPoolExecutor(max_workers=2) as pool:
            changes = list(pool.map(lambda args: update_one(*args),
                                    (("action_identity", "public"), ("decision_json", '{"x":1}'))))
        assert changes == [1, 1]
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("select email_action_identity,email_decision_json from actions where "
                              "record_type='email_unsubscribe_cancellations' and legacy_key='1'").fetchone() == (
                                  "public", '{"x":1}'
                              )


def test_integer_update_predicate_and_no_target_binding_match_source():
    from app.action_storage import insert_action, insert_email_action, update_action

    source, target = _databases()
    with closing(source), closing(target):
        cases = _cases()
        for kind in ("email_action_plans", "email_actions"):
            values = cases[kind]
            source.execute(f"insert into {kind}({','.join(values)}) values({','.join('?' for _ in values)})",
                           tuple(values.values()))
            insert_email_action(target, kind, values)
        values = cases["email_action_attempts"]
        source.execute(f"insert into email_action_attempts({','.join(values)}) values({','.join('?' for _ in values)})",
                       tuple(values.values()))
        insert_action(target, "email_action_attempts", values)
        assert update_action(target, "email_action_attempts", 1, {"error": "changed"},
                             where_sql="status=?", parameters=("missing",)).rowcount == 0
        assert update_action(target, "email_action_attempts", 1, {"error": "changed"},
                             where_sql="status=?", parameters=("done",)).rowcount == 1
        source.execute("update email_action_attempts set error='changed' where id=1 and status='done'")
        assert target.execute(email_action_projection("email_action_attempts")).fetchone() == source.execute(
            "select id,action_id,attempt_number,status,provider_operation,provider_target,"
            "provider_result_id,error,started_at,finished_at from email_action_attempts"
        ).fetchone()
        with pytest.raises(sqlite3.ProgrammingError):
            update_action(target, "email_action_attempts", 999, {"error": object()})


def test_ignore_invalid_check_and_retry_duplicate_match_native():
    from app.action_storage import insert_action

    source, target = _databases()
    with closing(source), closing(target):
        source.commit()
        target.commit()
        source.execute("pragma foreign_keys=off")
        target.execute("pragma foreign_keys=off")
        retry = _cases()["email_direct_retry_requests"]
        fields = ",".join(retry)
        marks = ",".join("?" for _ in retry)
        source.execute(f"insert into email_direct_retry_requests({fields}) values({marks})", tuple(retry.values()))
        insert_action(target, "email_direct_retry_requests", retry)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            source.execute(f"insert or ignore into email_direct_retry_requests({fields}) values({marks})",
                           tuple(retry.values()))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            insert_action(target, "email_direct_retry_requests", retry, ignore=True)
        bad = _cases()["email_unsubscribe_receipts"] | {"outcome": "invalid"}
        fields = ",".join(bad)
        marks = ",".join("?" for _ in bad)
        source.execute(f"insert or ignore into email_unsubscribe_receipts({fields}) values({marks})",
                       tuple(bad.values()))
        from app.action_storage import insert_email_action
        assert insert_email_action(target, "email_unsubscribe_receipts", bad, ignore=True).rowcount == 0
        assert source.execute("select count(*) from email_unsubscribe_receipts").fetchone() == (0,)


def test_rowid_max_uses_native_allocator_and_email_kinds_are_separate():
    from app.action_storage import insert_email_action

    source, target = _databases()
    with closing(source), closing(target):
        source.commit()
        target.commit()
        source.execute("pragma foreign_keys=off")
        target.execute("pragma foreign_keys=off")
        maximum = 2**63 - 1
        for kind, values in (("email_unsubscribe_cancellations", {"decision_json": "{}"}),
                             ("email_feedback_requests", _cases()["email_feedback_requests"])):
            fields = ",".join(values)
            marks = ",".join("?" for _ in values)
            source.execute(f"insert into {kind}(rowid,{fields}) values(?,{marks})", (maximum, *values.values()))
            assert insert_email_action(target, kind, values, source_rowid=maximum).lastrowid == maximum
        native = source.execute("insert into email_unsubscribe_cancellations(decision_json) values('{}')")
        result = insert_email_action(target, "email_unsubscribe_cancellations", {"decision_json": "{}"})
        assert 1 <= native.lastrowid < maximum
        assert 1 <= result.lastrowid < maximum
        assert result.lastrowid != maximum
        with pytest.raises(ValueError, match="unknown"):
            insert_email_action(target, "email_unsubscribe_cancellations", {"decision_json": "{}", "surprise": 1})


def test_email_max_lookup_cost_stays_bounded_as_one_family_grows():
    from app.action_storage import _email_max_id

    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table tasks(id integer primary key, record_type text)")
        db.execute("create table runs(id integer primary key, record_type text)")
        db.execute("create table email_classifications(id integer primary key)")
        create_action_schema(db, include_reply=True, reply_variant="fresh_memory_fk", include_email=True, compact_payload=True)
        from app.action_storage import insert_email_action
        values = {"action_identity": None, "decision_json": "{}"}
        insert_email_action(db, "email_unsubscribe_cancellations", values, source_rowid=100000)

        def measured_max():
            steps = 0
            def count():
                nonlocal steps
                steps += 1
                return 0
            db.set_progress_handler(count, 1)
            try:
                result = _email_max_id(db, "email_unsubscribe_cancellations")
            finally:
                db.set_progress_handler(None, 0)
            return result, steps

        small = measured_max()
        for index in range(1, 1001):
            insert_email_action(db, "email_unsubscribe_cancellations", values, source_rowid=index)
        large = measured_max()
        assert small[0] == large[0] == 100000
        assert large[1] <= small[1] + 50


def test_autoincrement_max_aborts_whole_caller_transaction_like_source():
    from app.action_storage import insert_action, insert_email_action

    source, target = _databases()
    with closing(source), closing(target):
        source.commit()
        target.commit()
        source.execute("pragma foreign_keys=off")
        target.execute("pragma foreign_keys=off")
        maximum = 2**63 - 1
        kind = "email_action_attempts"
        values = _cases()[kind]
        fields = ",".join(values)
        marks = ",".join("?" for _ in values)
        source.execute(f"insert into {kind}(id,{fields}) values(?,{marks})", (maximum, *values.values()))
        insert_action(target, kind, {"id": maximum} | values)
        source.execute("insert into email_unsubscribe_cancellations(decision_json) values('{}')")
        insert_email_action(target, "email_unsubscribe_cancellations", {"decision_json": "{}"})
        with pytest.raises(sqlite3.OperationalError, match="full"):
            source.execute(f"insert into {kind}({fields}) values({marks})", tuple(values.values()))
        with pytest.raises(sqlite3.OperationalError, match="full"):
            insert_action(target, kind, values)
        assert not source.in_transaction and not target.in_transaction
        assert source.execute("select count(*) from email_unsubscribe_cancellations").fetchone() == (0,)
        assert target.execute("select count(*) from actions where record_type='email_unsubscribe_cancellations'").fetchone() == (0,)
