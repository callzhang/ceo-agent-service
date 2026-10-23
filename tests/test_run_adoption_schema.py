import pytest

from app.run_adoption_schema import _translate_weak_run_ref


def test_weak_run_reference_preserves_null_and_zero_sentinels():
    run_map = {7: 9007}

    assert _translate_weak_run_ref(run_map, None) is None
    assert _translate_weak_run_ref(run_map, 0) == 0
    assert _translate_weak_run_ref(run_map, 7) == 9007


def test_weak_run_reference_rejects_unresolved_positive_identity():
    with pytest.raises(ValueError, match="unresolved or ambiguous source Run reference"):
        _translate_weak_run_ref({7: 9007}, 8)


def test_selection_branch_is_derived_from_exact_bound_candidate_option():
    from app.run_adoption_schema import _selection_branch

    candidate = '{"decision_options":[{"key":"keep","value":{"x":1}}]}'
    assert _selection_branch(candidate, "keep") == {
        "key": "keep", "value": {"x": 1}
    }
    with pytest.raises(ValueError, match="occur exactly once"):
        _selection_branch(candidate, "missing")


def test_action_sequence_restores_source_highwater_after_retired_maximum_is_removed():
    import sqlite3

    from app.run_adoption_schema import _restore_sequence_rows

    db = sqlite3.connect(":memory:")
    db.execute("create table actions(id integer primary key autoincrement, record_type text)")
    db.execute("insert into actions(id,record_type) values(7,'retained'),(101,'retired')")
    db.execute("delete from actions where id=101")
    captured = [tuple(row) for row in db.execute(
        "select rowid,name,seq from sqlite_sequence where name='actions'")]
    assert captured == [(1, "actions", 101)]
    db.execute("drop table actions")
    db.execute("create table actions(id integer primary key autoincrement, record_type text)")
    db.execute("insert into actions(id,record_type) values(7,'retained')")

    _restore_sequence_rows(db, captured, ("actions",))
    new_id = db.execute("insert into actions(record_type) values('new') returning id").fetchone()[0]

    assert new_id == 102
