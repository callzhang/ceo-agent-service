"""Point-write defaults must use the same SQL statement as their write."""
import pytest

from app.state_definitions import STATE_DEFAULTS, STATE_NOT_NULL, _quote
from app.state_storage import insert_state, update_state
from tests.test_state_storage import (
    facts, native_insert, pair as pair, required,
)


CLOCKS = tuple(
    (kind, field)
    for kind, defaults in STATE_DEFAULTS.items()
    for field, sql in defaults.items() if sql.lower() == "current_timestamp"
)


def witness(db, table, field, operation):
    phase = ["2033-04-05 06:07:07"]
    db.create_function("current_timestamp", 0, lambda: phase[0])
    db.execute("create table clock_witness(stored,statement_clock)")
    db.execute(
        "create trigger clock_witness_trigger after " + operation + " on " + table +
        " begin insert into clock_witness values(new." + _quote(field) +
        ",current_timestamp); end"
    )

    def trace(sql):
        phase[0] = ("2033-04-05 06:07:08" if
                    sql.lstrip().lower().startswith(("insert", "update")) else
                    "2033-04-05 06:07:07")
    db.set_trace_callback(trace)


@pytest.mark.parametrize("kind,field", CLOCKS)
@pytest.mark.parametrize("mode", ("insert", "ignore", "replace-null", "replace-nan"))
def test_insert_default_belongs_to_final_statement(pair, kind, field, mode):
    source, target, variant = pair
    values = required(variant, kind, "new")
    for name, sql in STATE_DEFAULTS[kind].items():
        if sql.lower() == "current_timestamp":
            values.pop(name, None)
    if mode.startswith("replace"):
        values[field] = None if mode == "replace-null" else float("nan")
    witness(source, _quote(kind), field, "insert")
    witness(target, "state", field, "insert")
    columns = tuple(values)
    flags = {"ignore": mode == "ignore", "replace": mode.startswith("replace")}
    sql = ("insert " + ("or replace " if flags["replace"] else
                       "or ignore " if flags["ignore"] else "") +
           "into " + _quote(kind) + "(" + ",".join(map(_quote, columns)) +
           ") values(" + ",".join("?" for _ in columns) + ")")
    cursor = source.execute(sql, tuple(values.values()))
    result = insert_state(target, kind, values, **flags)
    assert result.rowcount == cursor.rowcount == 1
    assert result.lastrowid == cursor.lastrowid
    for db in (source, target):
        assert db.execute("select * from clock_witness").fetchall() == [
            ("2033-04-05 06:07:08", "2033-04-05 06:07:08")
        ]
    assert facts(source, kind) == facts(target, kind, True)


@pytest.mark.parametrize("kind,field", CLOCKS)
@pytest.mark.parametrize("null", (None, float("nan")))
def test_point_replace_default_belongs_to_final_statement(pair, kind, field, null):
    source, target, variant = pair
    if field not in STATE_NOT_NULL[kind]:
        pytest.skip("SQLite only substitutes defaults for NOT NULL columns")
    values = required(variant, kind, "existing")
    native_insert(source, kind, values, -7)
    insert_state(target, kind, values, source_rowid=-7)
    witness(source, _quote(kind), field, "update")
    witness(target, "state", field, "update")
    assert source.execute(
        "update or replace " + _quote(kind) + " set " + _quote(field) +
        "=? where rowid=-7", (null,),
    ).rowcount == 1
    assert update_state(target, kind, -7, {field: null}, replace=True) == 1
    for db in (source, target):
        assert db.execute("select * from clock_witness").fetchall() == [
            ("2033-04-05 06:07:08", "2033-04-05 06:07:08")
        ]
    assert facts(source, kind) == facts(target, kind, True)


def test_guard_miss_skips_default_and_preserves_caller_work(pair):
    source, target, variant = pair
    values = required(variant, "service_state", "existing")
    native_insert(source, "service_state", values, -7)
    insert_state(target, "service_state", values, source_rowid=-7)
    calls = []
    for db in (source, target):
        db.create_function("current_timestamp", 0, lambda: calls.append("clock") or "t")
        db.execute("create table prior_work(value)")
        db.execute("insert into prior_work values('keep')")
    assert source.execute(
        "update or replace service_state set updated_at=null where rowid=-7 and key='absent'"
    ).rowcount == 0
    assert update_state(target, "service_state", -7, {"updated_at": None},
                        replace=True, where_sql="key='absent'") == 0
    assert calls == []
    for db in (source, target):
        assert db.in_transaction
        assert db.execute("select value from prior_work").fetchone()[0] == "keep"
