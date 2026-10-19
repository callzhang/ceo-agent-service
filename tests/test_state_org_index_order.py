"""Native organization secondary-index ties use original SQLite ROWID."""
import pytest

from app.state_definitions import state_projection
from app.state_storage import insert_state
from tests.test_state_storage import native_insert, pair as pair, required


@pytest.mark.parametrize("field", ("open_dingtalk_id", "name"))
@pytest.mark.parametrize("allocation", ("reverse_explicit", "max_auto"))
def test_org_lookup_ties_follow_source_rowid(pair, field, allocation):
    source, target, variant = pair
    maximum = 2**63 - 1
    kind = "org_user_profiles"
    for position in range(2):
        values = required(variant, kind, str(position)) | {
            "user_id": None if field == "name" else str(position),
            "name": "duplicate-name", "open_dingtalk_id": "duplicate-open",
            "title": "old" if position == 0 else "new",
        }
        if position == 0:
            native_insert(source, kind, values, maximum)
            insert_state(target, kind, values, source_rowid=maximum)
        elif allocation == "reverse_explicit":
            native_insert(source, kind, values, 7)
            insert_state(target, kind, values, source_rowid=7)
        else:
            assert native_insert(source, kind, values).lastrowid < maximum
            assert insert_state(target, kind, values).lastrowid < maximum
    projection = "(" + state_projection(kind) + ")"
    suffix = " order by user_id" if field == "name" else ""
    value = "duplicate-name" if field == "name" else "duplicate-open"
    sql = "select title from {} where " + field + "=?" + suffix
    native = source.execute(sql.format(kind), (value,)).fetchall()
    current = target.execute(sql.format(projection), (value,)).fetchall()
    assert native == [("new",), ("old",)]
    assert current == native
    plan = target.execute(
        "explain query plan " + sql.format(projection), (value,)
    ).fetchall()
    index = "idx_state_org_name" if field == "name" else "idx_state_org_open_id"
    assert any(index in row[3] for row in plan)
